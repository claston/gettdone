"""The optimized harness still exercises real sockets, lifespan and isolated clients."""
import asyncio
import threading
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse

from tests.http_runtime import LoopbackClient, WorkerHTTPServer, loopback_request


def test_listener_reuse_preserves_per_test_lifespan_and_cookie_isolation():
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("start")
        yield {"generation": len(events)}
        events.append("stop")

    app = FastAPI(lifespan=lifespan)

    @app.get("/probe")
    def probe(request: Request):
        return {"generation": request.state.generation, "cookie": request.cookies.get("owner")}

    with WorkerHTTPServer() as server:
        with server.serve(app) as first_url, LoopbackClient(base_url=first_url) as client:
            client.cookies.set("owner", "first")
            assert client.get("/probe").json() == {"generation": 1, "cookie": "first"}
        assert events == ["start", "stop"]
        with server.serve(app) as second_url, LoopbackClient(base_url=second_url) as client:
            assert second_url == first_url
            assert client.get("/probe").json() == {"generation": 3, "cookie": None}
        assert events == ["start", "stop", "start", "stop"]
    assert not server.is_alive


def test_failed_test_releases_application_for_the_next_test():
    with WorkerHTTPServer() as server:
        with pytest.raises(ValueError, match="test failure"):
            with server.serve(FastAPI()):
                raise ValueError("test failure")
        with server.serve(FastAPI()) as url, LoopbackClient(base_url=url) as client:
            assert client.get("/missing").status_code == 404


def test_startup_failure_does_not_poison_the_reusable_server():
    @asynccontextmanager
    async def fail_startup(app):
        raise RuntimeError("startup failed")
        yield

    with WorkerHTTPServer() as server:
        with pytest.raises(RuntimeError, match="startup failed"):
            with server.serve(FastAPI(lifespan=fail_startup)):
                pytest.fail("failed startup cannot accept requests")
        with server.serve(FastAPI()) as url, LoopbackClient(base_url=url) as client:
            assert client.get("/missing").status_code == 404


@pytest.mark.parametrize("url", ["https://127.0.0.1/", "http://example.com/", "http://127.0.0.2/", "https://example.com/"])
def test_plain_http_transport_cannot_be_used_for_tls_or_external_hosts(url):
    with LoopbackClient() as client, pytest.raises(ValueError, match="loopback"):
        client.get(url)


def test_local_requests_ignore_proxy_environment_and_do_not_load_tls_certificates(monkeypatch):
    import ssl

    def unexpected_ca_load(*args, **kwargs):
        raise AssertionError("plain loopback HTTP does not need TLS certificates")

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setattr(ssl, "create_default_context", unexpected_ca_load)
    with WorkerHTTPServer() as server, server.serve(FastAPI()) as url:
        with LoopbackClient(base_url=url) as client:
            assert isinstance(client, httpx.Client)
            assert client.get("/missing").status_code == 404


def test_one_shot_requests_preserve_headers_query_body_and_response_after_client_close():
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        return {"body": await request.json(), "query": request.query_params["q"], "header": request.headers["x-test"]}

    with WorkerHTTPServer() as server, server.serve(app) as url:
        response = loopback_request("POST", url + "/echo", params={"q": "value"}, headers={"x-test": "header"},
                                    json={"amount": 5}, timeout=2)
        assert response.status_code == 200
        assert response.json() == {"body": {"amount": 5}, "query": "value", "header": "header"}


def test_background_work_finishes_before_lifespan_cleanup_and_next_application():
    events = []
    started = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        yield
        events.append("shutdown")

    async def background():
        started.set()
        await asyncio.sleep(0.05)
        events.append("background")

    app = FastAPI(lifespan=lifespan)

    @app.get("/work")
    async def work():
        return JSONResponse({"ok": True}, background=BackgroundTask(background))

    with WorkerHTTPServer() as server:
        with server.serve(app) as url, LoopbackClient(base_url=url) as client:
            assert client.get("/work").status_code == 200
            assert started.wait(timeout=2)
        assert events == ["background", "shutdown"]
        with server.serve(FastAPI()) as url, LoopbackClient(base_url=url) as client:
            assert client.get("/missing").status_code == 404
