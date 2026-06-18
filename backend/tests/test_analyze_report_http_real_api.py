from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
import uvicorn

from app.dependencies import get_access_control_service, get_report_service
from app.main import app


class FakeReportService:
    def __init__(self) -> None:
        self._tmp = NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False)
        self._tmp.write(b"test-report")
        self._tmp.flush()
        self._path = Path(self._tmp.name)
        self._owners: dict[str, tuple[str, str]] = {}

    def get_report_path(self, analysis_id: str) -> Path:
        if analysis_id != "an_http123":
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        return self._path

    def set_report_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        owner = self._owners.get(analysis_id)
        if owner is not None and owner != (identity_type, identity_id):
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError
        self._owners[analysis_id] = (identity_type, identity_id)

    def assert_report_owner(
        self,
        analysis_id: str,
        identity_type: str,
        identity_id: str,
        *,
        allow_unowned: bool = False,
    ) -> None:
        owner = self._owners.get(analysis_id)
        if owner is None:
            if allow_unowned:
                return
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError
        if owner != (identity_type, identity_id):
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError


class FakeAccessControlService:
    def resolve_identity(self, anonymous_fingerprint: str | None, user_token: str | None):
        class Identity:
            def __init__(self, identity_type: str, identity_id: str) -> None:
                self.identity_type = identity_type
                self.identity_id = identity_id

        token = (user_token or "").strip()
        fingerprint = (anonymous_fingerprint or "").strip()
        if token:
            return Identity("user", "usr_fake")
        if fingerprint:
            return Identity("anonymous", f"anon_{fingerprint}")
        from app.application import InvalidUserTokenError

        raise InvalidUserTokenError


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _run_http_server():
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()

    host = "127.0.0.1"
    port = _find_free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://{host}:{port}"
    with httpx.Client(timeout=2.0) as client:
        deadline = time.time() + 15.0
        while time.time() < deadline:
            try:
                response = client.get(f"{base_url}/health")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            server.should_exit = True
            thread.join(timeout=5.0)
            app.dependency_overrides.clear()
            raise RuntimeError("HTTP test server did not start in time")

    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        app.dependency_overrides.clear()


def test_http_post_analyze_route_is_disabled() -> None:
    with _run_http_server() as base_url:
        response = httpx.post(
            f"{base_url}/analyze",
            data={"anonymous_fingerprint": "fp-http"},
            files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
            timeout=5.0,
        )

    assert response.status_code == 405


def test_http_get_report_negative_path_for_unknown_analysis_id() -> None:
    with _run_http_server() as base_url:
        response = httpx.get(
            f"{base_url}/report/an_unknown",
            params={"anonymous_fingerprint": "fp-owner"},
            timeout=5.0,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Analysis not found"
