from __future__ import annotations

import csv
import socket
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import uvicorn

from app.application import AccessControlService
from app.dependencies import get_access_control_service, get_report_service
from app.main import app


class _InMemoryConnCtx:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _AccessControlServiceInMemory(AccessControlService):
    def __init__(self, **kwargs) -> None:
        self._test_conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._test_conn.row_factory = sqlite3.Row
        super().__init__(**kwargs)

    def _connect(self) -> _InMemoryConnCtx:
        return _InMemoryConnCtx(self._test_conn)


class FakeReportService:
    def __init__(self) -> None:
        self._owners = {"an_convert_http_edits": None}
        self._updated_at = "2026-05-12T12:00:00Z"
        self._rows = [
            {"row_id": "row_1", "date": "2026-04-01", "description": "DEBIT A", "amount": -20.0, "is_deleted": False},
            {"row_id": "row_2", "date": "2026-04-02", "description": "CREDIT B", "amount": 100.0, "is_deleted": False},
        ]

    def assert_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        if analysis_id not in self._owners:
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        owner = self._owners.get(analysis_id)
        if owner is None:
            self._owners[analysis_id] = (identity_type, identity_id)
            return
        if owner != (identity_type, identity_id):
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError

    def apply_convert_edits(self, analysis_id: str, edits: list[dict], expected_updated_at: str | None = None):
        if analysis_id not in self._owners:
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        if expected_updated_at is not None and expected_updated_at != self._updated_at:
            from app.application import AnalysisEditConflictError

            raise AnalysisEditConflictError

        for edit in edits:
            if edit.get("row_id") == "row_1":
                credit = edit.get("credit")
                debit = edit.get("debit")
                if credit is not None and debit is not None:
                    raise ValueError("Provide only one of credit or debit")
                amount = float(credit) if credit is not None else (-float(debit) if debit is not None else self._rows[0]["amount"])
                self._rows[0]["date"] = edit.get("date") or self._rows[0]["date"]
                self._rows[0]["description"] = edit.get("description") or self._rows[0]["description"]
                self._rows[0]["amount"] = amount

        self._updated_at = "2026-05-12T12:05:00Z"
        active = [r for r in self._rows if not r["is_deleted"]]
        return {
            "processing_id": analysis_id,
            "transactions_total": len(active),
            "total_inflows": sum(r["amount"] for r in active if r["amount"] > 0),
            "total_outflows": sum(r["amount"] for r in active if r["amount"] < 0),
            "net_total": sum(r["amount"] for r in active),
            "preview_transactions": [
                {
                    "date": r["date"],
                    "description": r["description"],
                    "amount": r["amount"],
                    "category": "Outros",
                    "reconciliation_status": "unmatched",
                    "is_deleted": r["is_deleted"],
                }
                for r in self._rows
            ],
            "updated_at": self._updated_at,
        }

    def get_convert_report_path(self, processing_id: str, file_format: str = "csv") -> Path:
        if processing_id not in self._owners:
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        if file_format != "csv":
            _, path = tempfile.mkstemp(suffix=".ofx")
            Path(path).write_text("<OFX></OFX>", encoding="utf-8")
            return Path(path)
        _, path = tempfile.mkstemp(suffix=".csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "description", "amount"])
            for row in self._rows:
                if not row["is_deleted"]:
                    writer.writerow([row["date"], row["description"], row["amount"]])
        return Path(path)

    def get_upload_filename(self, processing_id: str) -> str:
        if processing_id not in self._owners:
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        return "extrato_nubank.pdf"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _run_http_server():
    access_control = _AccessControlServiceInMemory(
        state_file=Path("access-control-state.json"),
        token_secret="test-secret",
    )
    report_service = FakeReportService()

    app.dependency_overrides[get_report_service] = lambda: report_service
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


def test_http_convert_edits_happy_path_and_csv_reflects_edit() -> None:
    with _run_http_server() as base_url:
        edit = httpx.post(
            f"{base_url}/convert-edits/an_convert_http_edits",
            params={"anonymous_fingerprint": "fp-owner"},
            json={
                "edits": [
                    {
                        "row_id": "row_1",
                        "date": "2026-04-05",
                        "description": "EDITED CREDIT",
                        "credit": 45.75,
                        "debit": None,
                    }
                ]
            },
            timeout=5.0,
        )

        report = httpx.get(
            f"{base_url}/convert-report/an_convert_http_edits",
            params={"format": "csv", "anonymous_fingerprint": "fp-owner"},
            timeout=5.0,
        )

    assert edit.status_code == 200
    payload = edit.json()
    assert payload["preview_transactions"][0]["description"] == "EDITED CREDIT"
    assert payload["preview_transactions"][0]["amount"] == 45.75
    assert report.status_code == 200
    assert "2026-04-05,EDITED CREDIT,45.75" in report.text


def test_http_convert_edits_negative_path_conflict_on_stale_version() -> None:
    with _run_http_server() as base_url:
        first = httpx.post(
            f"{base_url}/convert-edits/an_convert_http_edits",
            params={"anonymous_fingerprint": "fp-owner"},
            json={
                "edits": [
                    {
                        "row_id": "row_1",
                        "date": "2026-04-05",
                        "description": "FIRST",
                        "credit": 10.0,
                        "debit": None,
                    }
                ]
            },
            timeout=5.0,
        )
        assert first.status_code == 200

        conflict = httpx.post(
            f"{base_url}/convert-edits/an_convert_http_edits",
            params={"anonymous_fingerprint": "fp-owner"},
            json={
                "expected_updated_at": "2026-01-01T00:00:00Z",
                "edits": [
                    {
                        "row_id": "row_1",
                        "date": "2026-04-06",
                        "description": "STALE",
                        "credit": 15.0,
                        "debit": None,
                    }
                ],
            },
            timeout=5.0,
        )

    assert conflict.status_code == 409
    assert "changed since last load" in conflict.json()["detail"]


def test_http_convert_edits_negative_path_rejects_other_owner() -> None:
    with _run_http_server() as base_url:
        prime = httpx.post(
            f"{base_url}/convert-edits/an_convert_http_edits",
            params={"anonymous_fingerprint": "fp-owner"},
            json={
                "edits": [
                    {
                        "row_id": "row_1",
                        "date": "2026-04-06",
                        "description": "PRIME",
                        "credit": 15.0,
                        "debit": None,
                    }
                ]
            },
            timeout=5.0,
        )
        assert prime.status_code == 200

        denied = httpx.post(
            f"{base_url}/convert-edits/an_convert_http_edits",
            params={"anonymous_fingerprint": "fp-other"},
            json={
                "edits": [
                    {
                        "row_id": "row_1",
                        "date": "2026-04-06",
                        "description": "DENIED",
                        "credit": 15.0,
                        "debit": None,
                    }
                ]
            },
            timeout=5.0,
        )

    assert denied.status_code == 403
    assert denied.json()["detail"] == "Access denied for this analysis."
