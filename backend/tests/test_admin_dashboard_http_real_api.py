from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import uvicorn

from app.application.access_control import AccessControlService
from app.dependencies import get_access_control_service
from app.main import app


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _run_http_server(tmp_path: Path):
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
        now_provider=lambda: datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc),
    )
    admin = access_control.register_user(
        name="Admin",
        email="admin@example.com",
        password="admin-pass",
    )
    access_control.record_user_conversion(
        user_id=admin.user_id,
        processing_id="an_http_dashboard",
        filename="extrato.pdf",
        model="Nubank",
        conversion_type="pdf-ofx",
        status="Sucesso",
        transactions_count=12,
        duration_ms=1400,
        created_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc).isoformat(),
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control

    host = "127.0.0.1"
    port = _find_free_port()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"

    with httpx.Client(timeout=2.0) as client:
        deadline = time.time() + 15.0
        while time.time() < deadline:
            try:
                if client.get(f"{base_url}/health").status_code == 200:
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
        access_control.close()


def test_admin_dashboard_real_http_requires_session_and_returns_metrics(tmp_path: Path) -> None:
    with _run_http_server(tmp_path) as base_url:
        with httpx.Client(timeout=5.0) as client:
            unauthorized = client.get(f"{base_url}/admin/dashboard")
            login = client.post(
                f"{base_url}/admin/auth/login",
                json={"email": "admin@example.com", "password": "admin-pass"},
            )
            dashboard = client.get(
                f"{base_url}/admin/dashboard",
                params={"days": 7, "identity_type": "registered"},
            )

    assert unauthorized.status_code == 401
    assert login.status_code == 200
    assert dashboard.status_code == 200
    assert dashboard.headers["cache-control"] == "no-store"
    assert dashboard.json()["summary"]["conversions_total"] == 1
