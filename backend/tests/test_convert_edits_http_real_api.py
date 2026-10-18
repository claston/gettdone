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
import pytest
import uvicorn

from app.application import AccessControlService
from app.dependencies import get_access_control_service, get_report_service
from app.main import app

pytestmark = pytest.mark.real_http


def test_http_edit_operation_survives_lost_response_and_rejects_other_owner(tmp_path) -> None:
    from app.application import ReportService, TempAnalysisStorage
    from app.application.models import AnalysisData, TransactionRow

    def configure(access):
        storage = TempAnalysisStorage(root_dir=tmp_path / "results", ttl_seconds=3600)
        rows = [TransactionRow(date="2026-04-01", description="Original", amount=10,
                               category="Outros", reconciliation_status="unmatched")]
        storage.save_analysis(AnalysisData(analysis_id="an_receipt_http", file_type="pdf", upload_filename="extrato.pdf",
                                          transactions_total=1,
                                          total_inflows=10, total_outflows=0, net_total=10,
                                          preview_transactions=rows, report_transactions=rows))
        owner = access.resolve_identity(anonymous_fingerprint="fp-owner", user_token=None)
        storage.set_convert_owner("an_receipt_http", owner.identity_type, owner.identity_id)
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    with _run_http_server(configure) as base_url:
        url = f"{base_url}/convert-edits/an_receipt_http"
        params = {"anonymous_fingerprint": "fp-owner"}
        payload = {"operation_id": "http-save-1", "edits": [{"action": "insert", "insert_position": 0,
                   "date": "2026-04-02", "description": "Incluído", "credit": 20}]}
        first = httpx.post(url, params=params, json=payload)
        confirmed = httpx.get(f"{url}/operations/http-save-1", params=params)
        repeated = httpx.post(url, params=params, json=payload)
        denied = httpx.get(f"{url}/operations/http-save-1", params={"anonymous_fingerprint": "fp-other"})
        report = httpx.get(f"{base_url}/convert-report/an_receipt_http", params={**params, "format": "csv"})
    assert first.status_code == confirmed.status_code == repeated.status_code == report.status_code == 200
    assert first.json() == confirmed.json() == repeated.json()
    assert first.json()["transactions_total"] == 2
    assert denied.status_code == 403
    assert "Incluído" in report.text


def test_http_stable_transaction_id_preserves_source_order_after_edit_and_insert(tmp_path) -> None:
    from app.application import ReportService, TempAnalysisStorage
    from app.application.conversion_pipeline import ConversionPipeline

    def configure(access):
        data = ConversionPipeline().run(filename="extrato.csv", raw_bytes=(
            b"date,description,amount\n2026-04-30,PRIMEIRO,10\n2026-04-30,SEGUNDO,-2\n2026-04-29,TERCEIRO,5\n"
        ), analysis_id="an_order_http").analysis_data
        storage = TempAnalysisStorage(root_dir=tmp_path / "results")
        storage.save_analysis(data)
        owner = access.resolve_identity(anonymous_fingerprint="fp-owner", user_token=None)
        storage.set_convert_owner(data.analysis_id, owner.identity_type, owner.identity_id)
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    with _run_http_server(configure) as base_url:
        url = f"{base_url}/convert-edits/an_order_http"
        params = {"anonymous_fingerprint": "fp-owner"}
        snapshot = httpx.post(url, params=params, json={"edits": []}).json()
        first = snapshot["preview_transactions"][0]["transaction_id"]
        saved = httpx.post(url, params=params, json={"expected_updated_at": snapshot["updated_at"], "edits": [
            {"row_id": first, "date": "2026-04-01", "description": "CORRIGIDO", "credit": 10},
            {"action": "insert", "transaction_id": "manual-http", "anchor_transaction_id": first,
             "date": "2026-04-30", "description": "MANUAL", "credit": 8},
        ]})
        report = httpx.get(f"{base_url}/convert-report/an_order_http", params={**params, "format": "csv"})
    assert saved.status_code == report.status_code == 200
    rows = saved.json()["preview_transactions"]
    assert rows[0]["transaction_id"] == first
    assert rows[0]["source_sequence"] == 1
    assert rows[-1]["transaction_id"] == "manual-http"
    assert rows[-1]["source_sequence"] == 1
    assert rows[-1]["source_subsequence"] == 1
    assert [row["description"] for row in sorted(rows, key=lambda row: (row["source_sequence"], row["source_subsequence"]))] == [
        "CORRIGIDO", "MANUAL", "SEGUNDO", "TERCEIRO",
    ]


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

    def apply_convert_edits(
        self,
        analysis_id: str,
        edits: list[dict],
        expected_updated_at: str | None = None,
        opening_balance: float | None = None,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ):
        _ = (opening_balance, closing_balance, bank_branch, account_number, bank_code)
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

    def get_convert_report_path(
        self,
        processing_id: str,
        file_format: str = "csv",
        *,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> Path:
        _ = (closing_balance, bank_branch, account_number, bank_code)
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
def _run_http_server(configure=None):
    access_control = _AccessControlServiceInMemory(
        state_file=Path("access-control-state.json"),
        token_secret="test-secret",
    )
    report_service = FakeReportService()

    app.dependency_overrides[get_report_service] = lambda: report_service
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    if configure:
        configure(access_control)

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


def test_http_pro_save_can_be_reopened_from_batch_and_exported(monkeypatch) -> None:
    from types import SimpleNamespace

    from app.adapters.conversion.memory_batches import InMemoryConversionBatchRepository
    from app.application.conversion.contracts.documents import ConversionDocumentReference
    from app.application.conversion.contracts.jobs import ConversionJobResultReference
    from app.application.conversion.conversion_batch import ConversionBatch
    from app.application.conversion.conversion_job import ConversionJob
    from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
    from app.dependencies import get_conversion_batch_service, get_conversion_runtime_config

    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "https://pro.example.test")
    ids = {}

    def configure(access):
        owner = access.resolve_identity(anonymous_fingerprint="fp-owner", user_token=None)
        batch = ConversionBatch.create(identity_type=owner.identity_type, identity_id=owner.identity_id,
                                       files_count=1, idempotency_key="http-sync")
        job = ConversionJob.create(batch_id=batch.batch_id, identity=owner, idempotency_key="http-sync-job",
                                   document=ConversionDocumentReference(storage_key="doc_sync", filename="extrato.pdf",
                                                                        file_type="pdf", size_bytes=100, sha256_hex="0" * 64))
        repository = InMemoryConversionBatchRepository()
        repository.create(batch=batch, jobs=[job])
        repository.mark_uploaded(job.job_id)
        repository.mark_queued(job.job_id)
        repository.mark_running(job.job_id)
        repository.mark_completed(job.job_id, result=ConversionJobResultReference(
            analysis_id="an_convert_http_edits", payload={"analysis": {"bank_name": "Banco Teste"}},
        ))
        ids.update(batch_id=batch.batch_id, job_id=job.job_id)
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=repository)
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping({
            "CONVERSION_ARCHITECTURE_MODE": "async_aws",
        })

    with _run_http_server(configure) as base_url:
        saved = httpx.post(f"{base_url}/api/pro/convert-edits/an_convert_http_edits",
                           params={"anonymous_fingerprint": "fp-owner"},
                           headers={"X-OFX-Pro-Client": "web", "Origin": "https://pro.example.test"},
                           json={**ids, "edits": [{"row_id": "row_1", "date": "2026-04-05",
                                                   "description": "SALVO E REABERTO", "credit": 45}]})
        assert saved.status_code == 200
        reloaded = httpx.get(f"{base_url}/api/pro/conversion-batches/{ids['batch_id']}",
                            params={"anonymous_fingerprint": "fp-owner"})
        assert reloaded.status_code == 200
        analysis = reloaded.json()["jobs"][0]["result"]["analysis"]
        assert analysis["updated_at"] == saved.json()["updated_at"]
        assert analysis["preview_transactions"][0]["description"] == "SALVO E REABERTO"
        report = httpx.get(f"{base_url}/convert-report/an_convert_http_edits",
                          params={"anonymous_fingerprint": "fp-owner", "format": "csv"})
        assert report.status_code == 200
        assert "SALVO E REABERTO" in report.text
        denied = httpx.get(f"{base_url}/api/pro/conversion-batches/{ids['batch_id']}",
                          params={"anonymous_fingerprint": "fp-other"})
        assert denied.status_code == 404
