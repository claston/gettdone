"""Reusable real loopback listener; each use retains an independent ASGI lifespan.

Only test infrastructure imports this module. Requests still cross a TCP socket;
the HTTP-only client rejects TLS and non-loopback destinations before connecting.
"""
import asyncio
import socket
import threading
import time
from contextlib import contextmanager

import httpx
import uvicorn


class _LoopbackTransport(httpx.HTTPTransport):
    def __init__(self):
        # Plain HTTP has no TLS handshake. Never use this transport for HTTPS.
        super().__init__(verify=False, trust_env=False)

    def handle_request(self, request):
        if request.url.scheme != "http" or request.url.host != "127.0.0.1":
            raise ValueError("Test transport accepts only plain HTTP on the 127.0.0.1 loopback address")
        return super().handle_request(request)


class LoopbackClient(httpx.Client):
    def __init__(self, **kwargs):
        if any(key in kwargs for key in ("transport", "proxy", "mounts", "verify", "cert", "trust_env")):
            raise ValueError("The loopback test client owns its transport and proxy configuration")
        super().__init__(transport=_LoopbackTransport(), trust_env=False, **kwargs)


def loopback_request(method, url, **kwargs):
    with LoopbackClient() as client:
        return client.request(method, url, **kwargs)


class WorkerHTTPServer:
    def __init__(self):
        self._application = None
        self._lifespan = None
        self._state = {}
        self._requests = 0
        self._loop = None
        self._thread = None
        self._socket = None
        self._error = None
        self._lease = threading.Lock()
        self._server = uvicorn.Server(uvicorn.Config(self._dispatch, log_level="error", lifespan="off", interface="asgi3"))

    @property
    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    async def _dispatch(self, scope, receive, send):
        application = self._application
        if application is None:
            await send({"type": "http.response.start", "status": 503, "headers": []})
            await send({"type": "http.response.body", "body": b"No active test application"})
            return
        self._requests += 1
        try:
            await application({**scope, "state": {**self._state, **scope.get("state", {})}}, receive, send)
        finally:
            self._requests -= 1

    async def _run_async(self):
        self._loop = asyncio.get_running_loop()
        await self._server.serve(sockets=[self._socket])

    def _run(self):
        try:
            asyncio.run(self._run_async())
        except BaseException as exc:
            self._error = exc

    def _start(self):
        if self.is_alive:
            return
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.bind(("127.0.0.1", 0))
        self._socket.listen(128)
        self.url = f"http://127.0.0.1:{self._socket.getsockname()[1]}"
        self._thread = threading.Thread(target=self._run, name="pytest-loopback-http", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 15
        while not self._server.started:
            if self._error is not None or not self.is_alive:
                self.close()
                raise RuntimeError("Test HTTP listener failed to start") from self._error
            if time.monotonic() >= deadline:
                self.close()
                raise RuntimeError("Test HTTP listener startup timed out")
            time.sleep(0.005)

    def _await(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=15)
        except BaseException:
            future.cancel()
            raise

    async def _activate(self, application):
        lifespan = application.router.lifespan_context(application)
        state = await lifespan.__aenter__()
        self._lifespan = lifespan
        self._state = state or {}
        self._application = application

    async def _deactivate(self):
        self._application = None
        try:
            # Responses can finish before their Starlette background tasks do.
            async with asyncio.timeout(10):
                while self._requests:
                    await asyncio.sleep(0.005)
            await self._lifespan.__aexit__(None, None, None)
        finally:
            self._lifespan = None
            self._state = {}
            # Do not carry connections or cookies between test clients.
            for connection in list(self._server.server_state.connections):
                connection.shutdown()

    @contextmanager
    def serve(self, application):
        if not self._lease.acquire(blocking=False):
            raise RuntimeError("A test HTTP application is already active on this worker")
        try:
            self._start()
            self._await(self._activate(application))
            try:
                yield self.url
            finally:
                self._await(self._deactivate())
        finally:
            self._lease.release()

    def close(self):
        if self._thread is not None:
            self._server.should_exit = True
            self._thread.join(timeout=15)
            if self.is_alive:
                raise RuntimeError("Test HTTP listener did not stop")
        if self._socket is not None:
            self._socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


_worker_server = None


@contextmanager
def serve_http(application):
    global _worker_server
    if _worker_server is None:
        _worker_server = WorkerHTTPServer()
    with _worker_server.serve(application) as url:
        yield url


def close_worker_server():
    global _worker_server
    if _worker_server is not None:
        try:
            _worker_server.close()
        finally:
            _worker_server = None
