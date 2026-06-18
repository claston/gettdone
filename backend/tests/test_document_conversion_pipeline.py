from pathlib import Path
from types import SimpleNamespace

from app.application.conversion.conversion_pipeline_result import ConversionPipelineStatus
from app.application.conversion.document_conversion_pipeline import (
    DocumentConversionPipeline,
    DocumentConversionRequest,
    DocumentConversionRuntime,
    StagedUploadRef,
)
from app.application.conversion.document_preflight_service import DocumentPreflightResult
from app.application.conversion_pipeline import ConversionPipelineResult, OperationalPipelineSummary
from app.application.models import AnalysisData, NormalizedTransaction, TransactionRow


class FakeAccessControlService:
    def __init__(self) -> None:
        self.identity = SimpleNamespace(
            identity_type="user",
            identity_id="user_123",
            quota_limit=10,
            quota_mode="conversion",
            max_upload_size_bytes=1024 * 1024,
            max_pages_per_file=50,
            max_pages_per_file_ocr=10,
        )
        self.recorded_user_conversions: list[dict[str, object]] = []
        self.consumed_units: list[int] = []

    def resolve_identity(self, *, anonymous_fingerprint: str | None, user_token: str | None):
        _ = anonymous_fingerprint
        assert user_token == "user-token"
        return self.identity

    def assert_upload_size(self, data: bytes, *, max_upload_size_bytes: int) -> None:
        assert len(data) <= max_upload_size_bytes

    def ensure_quota_available(self, identity, *, required_units: int = 1) -> None:
        assert identity is self.identity
        assert required_units == 1

    def consume_quota(self, identity, *, consumed_units: int = 1) -> int:
        assert identity is self.identity
        self.consumed_units.append(consumed_units)
        return 9

    def record_user_conversion(self, **kwargs) -> None:
        self.recorded_user_conversions.append(kwargs)


class FakeReportService:
    def __init__(self) -> None:
        self.owners: list[tuple[str, str, str]] = []

    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        self.owners.append((analysis_id, identity_type, identity_id))


class FakeAnalysisRepository:
    def __init__(self) -> None:
        self.saved_analysis: AnalysisData | None = None

    def save_analysis(self, data: AnalysisData) -> str:
        self.saved_analysis = data
        return "2026-06-19T12:00:00+00:00"


class FakeProcessingPipeline:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def run_document(
        self,
        *,
        document,
        analysis_id: str,
        on_ocr_progress=None,
        max_ocr_pages: int | None = None,
    ) -> ConversionPipelineResult:
        self.calls.append(
            {
                "document": document,
                "analysis_id": analysis_id,
                "on_ocr_progress": on_ocr_progress,
                "max_ocr_pages": max_ocr_pages,
            }
        )
        analysis_data = AnalysisData(
            analysis_id=analysis_id,
            file_type="pdf",
            upload_filename=document.filename,
            semantic_type="bank_statement",
            semantic_confidence=0.98,
            semantic_evidence=["csv"],
            transactions_total=1,
            total_inflows=150.0,
            total_outflows=0.0,
            net_total=150.0,
            preview_transactions=[
                TransactionRow(
                    date="2026-06-18",
                    description="PIX RECEBIDO",
                    amount=150.0,
                    category="Outros",
                    reconciliation_status="unmatched",
                )
            ],
            report_transactions=[
                TransactionRow(
                    date="2026-06-18",
                    description="PIX RECEBIDO",
                    amount=150.0,
                    category="Outros",
                    reconciliation_status="unmatched",
                )
            ],
            updated_at="2026-06-18T15:00:00+00:00",
            bank_name="Itau",
            bank_code="341",
            pdf_processing_metrics={
                "total_ms": 12.4,
                "parse_ms": 4.2,
                "classify_ms": 1.3,
                "normalize_ms": 0.8,
                "reconcile_ms": 0.9,
                "page_count": 1,
                "extracted_char_count": 220,
                "flattened_line_count": 10,
                "grouped_transactions_count": 1,
                "inline_candidates_count": 1,
                "inline_transactions_count": 1,
                "selected_parser": "grouped",
                "export_recommendation": "review_recommended",
                "export_recommendation_reason": "low_confidence_band",
            },
        )
        return ConversionPipelineResult(
            analysis_data=analysis_data,
            document=document,
            parsed_document=SimpleNamespace(),
            classification=SimpleNamespace(),
            operational_summary=OperationalPipelineSummary(
                total_volume=150.0,
                inflow_count=1,
                outflow_count=0,
                reconciled_entries=0,
                unmatched_entries=1,
            ),
            top_expenses_rows=[
                NormalizedTransaction(
                    date="2026-06-18",
                    description="PIX RECEBIDO",
                    amount=150.0,
                    type="credit",
                )
            ],
            parse_ms=4.2,
        )


class FailingAnalyzeFallbackService:
    def analyze(self, **_kwargs):
        raise AssertionError("Fallback analyze service should not be called when processing_pipeline is available.")


def test_document_conversion_request_captures_preflight_flags() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"

    request = DocumentConversionRequest.from_inputs(
        filename="statement.pdf",
        staged_upload=StagedUploadRef(
            path=staged_path,
            size_bytes=staged_path.stat().st_size,
            sha256_hex="abc123",
        ),
        anonymous_fingerprint="anon-123",
        user_token="user-token",
        authorization="Bearer auth-token",
        access_cookie_token="cookie-token",
        on_ocr_progress=None,
        scanned_likely=True,
        estimated_pages_count=4,
    )

    assert request.filename == "statement.pdf"
    assert request.preflight_result == DocumentPreflightResult(
        scanned_likely=True,
        estimated_pages_count=4,
    )


def test_document_conversion_runtime_tracks_ocr_progress_and_forwards_callback() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    observed_progress: list[tuple[int, int]] = []
    request = DocumentConversionRequest.from_inputs(
        filename="statement.pdf",
        staged_upload=StagedUploadRef(
            path=staged_path,
            size_bytes=staged_path.stat().st_size,
            sha256_hex="abc123",
        ),
        anonymous_fingerprint=None,
        user_token="user-token",
        authorization=None,
        access_cookie_token=None,
        on_ocr_progress=lambda current_page, total_page_count: observed_progress.append(
            (current_page, total_page_count)
        ),
        scanned_likely=True,
        estimated_pages_count=3,
    )

    runtime = DocumentConversionRuntime.from_request(request)
    callback = runtime.build_ocr_progress_callback(request=request)
    callback(2, 3)
    callback(1, 3)

    assert runtime.ocr_pages_processed == 2
    assert observed_progress == [(2, 3), (1, 3)]


def test_document_conversion_pipeline_uses_processing_pipeline_when_available() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    access_control_service = FakeAccessControlService()
    report_service = FakeReportService()
    analysis_repository = FakeAnalysisRepository()
    processing_pipeline = FakeProcessingPipeline()
    pipeline = DocumentConversionPipeline(
        report_service=report_service,
        access_control_service=access_control_service,
        processing_pipeline=processing_pipeline,
        analysis_repository=analysis_repository,
        analyze_fallback_service=FailingAnalyzeFallbackService(),
    )

    response = pipeline.run(
        filename="statement.csv",
        staged_upload=StagedUploadRef(
            path=staged_path,
            size_bytes=staged_path.stat().st_size,
            sha256_hex="abc123",
        ),
        anonymous_fingerprint=None,
        user_token="user-token",
        authorization=None,
        access_cookie_token=None,
        scanned_likely=False,
        estimated_pages_count=None,
    )

    assert len(processing_pipeline.calls) == 1
    assert processing_pipeline.calls[0]["document"].filename == "statement.csv"
    assert response.status == ConversionPipelineStatus.COMPLETED
    assert response.payload is not None
    assert analysis_repository.saved_analysis is not None
    assert analysis_repository.saved_analysis.analysis_id == response.payload["processing_id"]
    assert response.payload["analysis"]["analysis_id"] == response.payload["processing_id"]
    assert response.payload["identity_type"] == "user"
    assert response.payload["quota_remaining"] == 9
    assert response.metadata is not None
    assert response.metadata["remaining_quota"] == 9
    assert response.metadata["page_count"] == 1
    assert report_service.owners == [(response.payload["processing_id"], "user", "user_123")]
    assert access_control_service.consumed_units == [1]
