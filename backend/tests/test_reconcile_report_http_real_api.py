from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest

from app.application import AccessControlService
from app.dependencies import get_access_control_service, get_report_service
from app.main import app
from tests.http_runtime import loopback_request

pytestmark = pytest.mark.real_http


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
        self._tmp = NamedTemporaryFile(mode="wb", suffix=".xlsx", delete=False)
        self._tmp.write(b"PK\x03\x04fake-xlsx")
        self._tmp.flush()
        self._path = Path(self._tmp.name)
        self._owners: dict[str, tuple[str, str]] = {}
        self._known_ids: set[str] = set()
        self._seq = 0

    def save_reconcile_report(self, summary, reconciliation_rows, problems):
        _ = (summary, reconciliation_rows, problems)
        self._seq += 1
        analysis_id = f"rc_http_{self._seq}"
        self._known_ids.add(analysis_id)
        return analysis_id, "2099-01-01T00:00:00Z"

    def set_reconcile_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._owners[analysis_id] = (identity_type, identity_id)

    def assert_reconcile_owner(self, analysis_id: str, identity_type: str, identity_id: str, *, allow_unowned: bool = False) -> None:
        if analysis_id not in self._known_ids:
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        owner = self._owners.get(analysis_id)
        if owner is None:
            if allow_unowned:
                return
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError
        if owner != (identity_type, identity_id):
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError

    def get_reconcile_report_path(self, analysis_id: str, file_format: str = "xlsx") -> Path:
        _ = file_format
        if analysis_id not in self._known_ids:
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        return self._path


@contextmanager
def _run_http_server():
    access_control = _AccessControlServiceInMemory(
        state_file=Path("access-control-state.json"),
        token_secret="test-secret",
    )
    report_service = FakeReportService()

    app.dependency_overrides[get_report_service] = lambda: report_service
    app.dependency_overrides[get_access_control_service] = lambda: access_control

    from tests.http_runtime import serve_http

    try:
        with serve_http(app) as base_url:
            yield base_url
    finally:
        app.dependency_overrides.clear()
        access_control._test_conn.close()


def test_http_reconcile_happy_path() -> None:
    with _run_http_server() as base_url:
        response = loopback_request("POST",
            f"{base_url}/reconcile",
            data={"anonymous_fingerprint": "fp-owner"},
            files={
                "bank_file": ("bank.csv", b"date,description,amount\n2026-04-01,TEST,-100", "text/csv"),
                "sheet_file": ("sheet.csv", b"data,valor,descricao\n2026-04-01,-120,TEST", "text/csv"),
            },
            timeout=5.0,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_id"].startswith("rc_http_")
    assert payload["status"] == "accepted"
    assert payload["bank_file_type"] == "csv"
    assert payload["sheet_file_type"] == "csv"


def test_http_reconcile_report_happy_path_after_intake() -> None:
    with _run_http_server() as base_url:
        intake = loopback_request("POST",
            f"{base_url}/reconcile",
            data={"anonymous_fingerprint": "fp-owner"},
            files={
                "bank_file": ("bank.csv", b"date,description,amount\n2026-04-01,TEST,-100", "text/csv"),
                "sheet_file": ("sheet.csv", b"data,valor,descricao\n2026-04-01,-120,TEST", "text/csv"),
            },
            timeout=5.0,
        )
        assert intake.status_code == 200
        analysis_id = intake.json()["analysis_id"]

        report = loopback_request("GET",
            f"{base_url}/reconcile-report/{analysis_id}",
            params={"anonymous_fingerprint": "fp-owner"},
            timeout=5.0,
        )

    assert report.status_code == 200
    assert report.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_http_reconcile_report_negative_path_rejects_other_owner() -> None:
    with _run_http_server() as base_url:
        intake = loopback_request("POST",
            f"{base_url}/reconcile",
            data={"anonymous_fingerprint": "fp-owner"},
            files={
                "bank_file": ("bank.csv", b"date,description,amount\n2026-04-01,TEST,-100", "text/csv"),
                "sheet_file": ("sheet.csv", b"data,valor,descricao\n2026-04-01,-120,TEST", "text/csv"),
            },
            timeout=5.0,
        )
        assert intake.status_code == 200
        analysis_id = intake.json()["analysis_id"]

        denied = loopback_request("GET",
            f"{base_url}/reconcile-report/{analysis_id}",
            params={"anonymous_fingerprint": "fp-other"},
            timeout=5.0,
        )

    assert denied.status_code == 403
    assert denied.json()["detail"] == "Access denied for this analysis."
