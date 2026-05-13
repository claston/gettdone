from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
import uvicorn

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


class FakeAnalyzeService:
    def analyze(self, filename: str, raw_bytes: bytes) -> AnalyzeResponse:
        if not filename.endswith((".csv", ".xlsx", ".ofx", ".pdf")):
            from app.application import UnsupportedFileTypeError

            raise UnsupportedFileTypeError

        return AnalyzeResponse(
            analysis_id="an_http123",
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
                total_ms=20.0,
                parse_ms=10.0,
                classify_ms=2.0,
                normalize_ms=3.0,
                reconcile_ms=5.0,
                page_count=2,
                extracted_char_count=500,
                flattened_line_count=24,
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
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
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


def test_http_post_analyze_happy_path_exposes_pdf_metrics_compatibility() -> None:
    with _run_http_server() as base_url:
        response = httpx.post(
            f"{base_url}/analyze",
            data={"anonymous_fingerprint": "fp-http"},
            files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
            timeout=5.0,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis_id"] == "an_http123"
    assert payload["pdf_processing_metrics"]["selected_parser"] == "grouped"
    assert payload["pdf_processing_metrics"]["canonical_transactions_count"] == 2
    assert payload["pdf_processing_metrics"]["confidence_band"] == "low"
    assert payload["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
    assert payload["pdf_processing_metrics"]["export_recommendation_reason"] == "low_confidence_band"
    assert payload["pdf_processing_metrics"]["canonical_warning_types_list"] == [
        "balance_consistency_failed"
    ]


def test_http_get_report_happy_path_after_analyze_owner_binding() -> None:
    with _run_http_server() as base_url:
        intake = httpx.post(
            f"{base_url}/analyze",
            data={"anonymous_fingerprint": "fp-owner"},
            files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
            timeout=5.0,
        )
        assert intake.status_code == 200

        report = httpx.get(
            f"{base_url}/report/an_http123",
            params={"anonymous_fingerprint": "fp-owner"},
            timeout=5.0,
        )

    assert report.status_code == 200


def test_http_get_report_negative_path_for_unknown_analysis_id() -> None:
    with _run_http_server() as base_url:
        response = httpx.get(
            f"{base_url}/report/an_unknown",
            params={"anonymous_fingerprint": "fp-owner"},
            timeout=5.0,
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Analysis not found"


def test_http_post_analyze_negative_path_for_unsupported_file_type() -> None:
    with _run_http_server() as base_url:
        response = httpx.post(
            f"{base_url}/analyze",
            data={"anonymous_fingerprint": "fp-http"},
            files={"file": ("sample.txt", b"unsupported", "text/plain")},
            timeout=5.0,
        )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]
