from __future__ import annotations

import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp

import httpx
import uvicorn

from app.application import AccessControlService
from app.dependencies import get_access_control_service, get_analyze_service, get_report_service
from app.main import app
from app.schemas import (
    AnalyzeResponse,
    BeforeAfterPreview,
    CategorySummary,
    Insight,
    OperationalSummary,
    PdfProcessingMetrics,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)


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


class FakeAnalyzeService:
    def analyze(self, filename: str, raw_bytes: bytes, on_ocr_progress=None) -> AnalyzeResponse:
        _ = on_ocr_progress
        if not filename.endswith((".csv", ".xlsx", ".ofx", ".pdf")):
            from app.application import UnsupportedFileTypeError

            raise UnsupportedFileTypeError

        return AnalyzeResponse(
            analysis_id="an_convert_http123",
            file_type="pdf",
            transactions_total=2,
            total_inflows=100.0,
            total_outflows=-20.0,
            net_total=80.0,
            operational_summary=OperationalSummary(
                total_volume=120.0,
                inflow_count=1,
                outflow_count=1,
                reconciled_entries=0,
                unmatched_entries=2,
            ),
            reconciliation=ReconciliationSummary(
                matched_groups=0,
                reversed_entries=0,
                potential_duplicates=0,
            ),
            categories=[CategorySummary(category="Outros", total=-20.0, count=1)],
            top_expenses=[
                TopExpense(
                    description="TEST",
                    amount=-20.0,
                    date="2026-04-01",
                    category="Outros",
                )
            ],
            insights=[Insight(type="test", title="Test insight", description=f"Bytes: {len(raw_bytes)}")],
            preview_transactions=[
                TransactionPreview(
                    date="2026-04-01",
                    description="TEST",
                    amount=-20.0,
                    category="Outros",
                    reconciliation_status="unmatched",
                )
            ],
            preview_before_after=[
                BeforeAfterPreview(
                    date="2026-04-01",
                    description_before="test",
                    description_after="TEST",
                    amount_before=-20.0,
                    amount_after=-20.0,
                )
            ],
            expires_at=None,
            pdf_processing_metrics=PdfProcessingMetrics(
                total_ms=21.0,
                parse_ms=11.0,
                classify_ms=2.0,
                normalize_ms=3.0,
                reconcile_ms=5.0,
                page_count=2,
                extracted_char_count=501,
                flattened_line_count=25,
                grouped_transactions_count=2,
                inline_candidates_count=2,
                inline_transactions_count=2,
                tabular_candidates_count=0,
                tabular_transactions_count=0,
                columnar_candidates_count=0,
                columnar_transactions_count=0,
                selected_parser="grouped",
                parser_selection_reason="grouped_rows_available",
                inline_decision="skipped_due_to_grouped",
                tabular_decision="skipped_due_to_grouped",
                columnar_decision="skipped_due_to_grouped",
                confidence_band="low",
                export_recommendation="review_recommended",
                export_recommendation_reason="low_confidence_band",
                balance_consistency_checked=1,
                balance_consistency_failed=0,
                canonical_transactions_count=2,
                canonical_with_running_balance_count=2,
                canonical_with_external_reference_count=2,
                canonical_warning_count=1,
                canonical_balance_warning_count=1,
                canonical_warning_transactions_count=1,
                canonical_warning_types_count=1,
                canonical_warning_types="balance_consistency_failed",
                canonical_warning_types_list=["balance_consistency_failed"],
                canonical_running_balance_coverage_rate=1.0,
                canonical_external_reference_coverage_rate=1.0,
                canonical_warning_transaction_rate=0.5,
            ),
        )


class FakeReportService:
    def __init__(self) -> None:
        self._tmp = NamedTemporaryFile(mode="wb", suffix=".ofx", delete=False)
        self._tmp.write(b"<OFX><BANKMSGSRSV1><STMTTRNRS></STMTTRNRS></BANKMSGSRSV1></OFX>")
        self._tmp.flush()
        self._path = Path(self._tmp.name)
        self._owners: dict[str, tuple[str, str]] = {}

    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self._owners[analysis_id] = (identity_type, identity_id)

    def assert_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        owner = self._owners.get(analysis_id)
        if owner is None:
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        if owner != (identity_type, identity_id):
            from app.application import AnalysisAccessDeniedError

            raise AnalysisAccessDeniedError

    def get_convert_report_path(
        self,
        processing_id: str,
        file_format: str = "ofx",
        *,
        closing_balance: float | None = None,
        bank_branch: str | None = None,
        account_number: str | None = None,
        bank_code: str | None = None,
    ) -> Path:
        _ = (closing_balance, bank_branch, account_number, bank_code)
        _ = file_format
        if processing_id not in self._owners:
            from app.application import AnalysisNotFoundError

            raise AnalysisNotFoundError
        return self._path

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
    state_dir = Path(mkdtemp(prefix="convert-http-real-"))
    access_control = _AccessControlServiceInMemory(
        state_file=state_dir / "access-control-state.json",
        token_secret="test-secret",
    )
    report_service = FakeReportService()

    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_report_service] = lambda: report_service

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


def test_http_convert_happy_path_exposes_canonical_pdf_metrics_compatibility() -> None:
    with _run_http_server() as base_url:
        response = httpx.post(
            f"{base_url}/convert",
            data={"anonymous_fingerprint": "fp-convert-owner"},
            files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
            timeout=5.0,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processing_id"] == "an_convert_http123"
    assert payload["analysis"]["pdf_processing_metrics"]["selected_parser"] == "grouped"
    assert payload["analysis"]["pdf_processing_metrics"]["parser_selection_reason"] == "grouped_rows_available"
    assert payload["analysis"]["pdf_processing_metrics"]["inline_decision"] == "skipped_due_to_grouped"
    assert payload["analysis"]["pdf_processing_metrics"]["tabular_decision"] == "skipped_due_to_grouped"
    assert payload["analysis"]["pdf_processing_metrics"]["columnar_decision"] == "skipped_due_to_grouped"
    assert payload["analysis"]["pdf_processing_metrics"]["canonical_transactions_count"] == 2
    assert payload["analysis"]["pdf_processing_metrics"]["confidence_band"] == "low"
    assert payload["analysis"]["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
    assert payload["analysis"]["pdf_processing_metrics"]["export_recommendation_reason"] == "low_confidence_band"
    assert payload["analysis"]["pdf_processing_metrics"]["canonical_warning_types_list"] == [
        "balance_consistency_failed"
    ]


def test_http_convert_report_happy_path_after_convert() -> None:
    with _run_http_server() as base_url:
        convert = httpx.post(
            f"{base_url}/convert",
            data={"anonymous_fingerprint": "fp-convert-owner"},
            files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
            timeout=5.0,
        )
        assert convert.status_code == 200

        report = httpx.get(
            f"{base_url}/convert-report/an_convert_http123",
            params={"format": "ofx", "anonymous_fingerprint": "fp-convert-owner"},
            timeout=5.0,
        )

    assert report.status_code == 200
    assert report.headers["content-type"].startswith("application/x-ofx")


def test_http_convert_report_negative_path_rejects_other_owner() -> None:
    with _run_http_server() as base_url:
        convert = httpx.post(
            f"{base_url}/convert",
            data={"anonymous_fingerprint": "fp-convert-owner"},
            files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
            timeout=5.0,
        )
        assert convert.status_code == 200

        denied = httpx.get(
            f"{base_url}/convert-report/an_convert_http123",
            params={"format": "ofx", "anonymous_fingerprint": "fp-other-owner"},
            timeout=5.0,
        )

    assert denied.status_code == 403
    assert denied.json()["detail"] == "Access denied for this analysis."
