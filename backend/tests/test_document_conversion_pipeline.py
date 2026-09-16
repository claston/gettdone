import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.application.access_control import IdentityContext
from app.application.conversion.conversion_document_store import ConversionDocumentReference
from app.application.conversion.conversion_job import ConversionExecutionHooks, ConversionJob
from app.application.conversion.conversion_pipeline_result import ConversionPipelineStatus
from app.application.conversion.document_conversion_pipeline import (
    DocumentConversionPipeline,
    DocumentConversionRuntime,
)
from app.application.conversion.document_extractor import ExtractedDocument
from app.application.conversion.document_preflight_service import DocumentPreflightResult, DocumentPreflightService
from app.application.conversion.statement_parser import ParsedBankStatement, ParsedTransaction
from app.application.conversion.uploaded_document import UploadedDocument, UploadedDocumentStage
from app.application.conversion_pipeline import ConversionPipelineResult, OperationalPipelineSummary
from app.application.document_extraction_models import ExtractedLine
from app.application.errors import InvalidFileContentError, MaxPagesPerFileExceededError
from app.application.models import AnalysisData, NormalizedTransaction, TransactionRow
from app.application.parsers.service import ParsedDocument


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

    def consume_quota(self, identity, *, consumed_units: int = 1, idempotency_key: str | None = None) -> int:
        assert identity is self.identity
        assert idempotency_key is not None
        self.consumed_units.append(consumed_units)
        return 9

    def record_user_conversion(self, **kwargs) -> None:
        self.recorded_user_conversions.append(kwargs)


class FakeAnonymousAccessControlService(FakeAccessControlService):
    def __init__(self) -> None:
        super().__init__()
        self.identity = SimpleNamespace(
            identity_type="anonymous",
            identity_id="anon_free_123",
            quota_limit=3,
            quota_mode="conversion",
            max_upload_size_bytes=1024 * 1024,
            max_pages_per_file=50,
            max_pages_per_file_ocr=10,
        )
        self.recorded_anonymous_conversions: list[dict[str, object]] = []

    def resolve_identity(self, *, anonymous_fingerprint: str | None, user_token: str | None):
        assert anonymous_fingerprint == "free-browser"
        assert not user_token
        return self.identity

    def record_anonymous_conversion_event(self, **kwargs) -> None:
        self.recorded_anonymous_conversions.append(kwargs)


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

    def run_parsed_document(
        self,
        *,
        document,
        parsed_document,
        analysis_id: str,
        parse_ms: float,
    ) -> ConversionPipelineResult:
        self.calls.append(
            {
                "document": document,
                "parsed_document": parsed_document,
                "analysis_id": analysis_id,
                "parse_ms": parse_ms,
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


class FakeDocumentExtractor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def extract(
        self,
        *,
        document,
        on_ocr_progress=None,
        max_ocr_pages: int | None = None,
        pdf_parser=None,
    ) -> ExtractedDocument:
        self.calls.append(
            {
                "document": document,
                "on_ocr_progress": on_ocr_progress,
                "max_ocr_pages": max_ocr_pages,
                "pdf_parser": pdf_parser,
            }
        )
        return ExtractedDocument(
            source_document=document,
            extracted_text="2026-06-18 PIX RECEBIDO 150,00",
            source_page_texts=("2026-06-18 PIX RECEBIDO 150,00",),
            metadata={
                "legacy_parsed_document": ParsedDocument(
                    file_type=document.file_type,
                    transactions=[
                        NormalizedTransaction(
                            date="2026-06-18",
                            description="PIX RECEBIDO",
                            amount=150.0,
                            type="credit",
                        )
                    ],
                    extracted_text="2026-06-18 PIX RECEBIDO 150,00",
                    source_page_texts=("2026-06-18 PIX RECEBIDO 150,00",),
                    source_layout_lines=((ExtractedLine(
                        id="line-1", page_number=1, line_index=1,
                        text="PIX RECEBIDO", bbox={"left": 0.12, "top": 0.2, "width": 0.3, "height": 0.03},
                    ),),),
                    parse_metrics={"page_count": 1, "selected_parser": "grouped"},
                )
            },
        )


class FakeOcrDocumentExtractor(FakeDocumentExtractor):
    def extract(self, **kwargs) -> ExtractedDocument:
        result = super().extract(**kwargs)
        on_ocr_progress = kwargs.get("on_ocr_progress")
        assert on_ocr_progress is not None
        on_ocr_progress(1, 1)
        return result


class FakeStatementParser:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def parse(self, *, extracted_document: ExtractedDocument) -> ParsedBankStatement:
        self.calls.append({"extracted_document": extracted_document})
        return ParsedBankStatement(
            file_type=extracted_document.source_document.file_type,
            transactions=[
                ParsedTransaction(
                    date="2026-06-18",
                    description="PIX RECEBIDO",
                    amount=150.0,
                    type="credit",
                )
            ],
            extracted_document=extracted_document,
            extracted_text=extracted_document.extracted_text,
            source_page_texts=extracted_document.source_page_texts,
            metadata=dict(extracted_document.metadata or {}),
        )


class RecordingDocumentPreflightService(DocumentPreflightService):
    def __init__(self) -> None:
        self.inspections: list[tuple[str, bytes]] = []

    def inspect_raw_bytes(self, *, filename: str, raw_bytes: bytes) -> DocumentPreflightResult:
        self.inspections.append((filename, raw_bytes))
        return DocumentPreflightResult(scanned_likely=True, estimated_pages_count=4)


class RecordingCanonicalLayoutCapture:
    def __init__(self, *, fail: bool = False, failure_capture_enabled: bool = False) -> None:
        self.fail = fail
        self.failure_capture_enabled = failure_capture_enabled
        self.calls: list[dict[str, object]] = []

    def capture(self, *, document, **quality_values):
        self.calls.append({"document": document, **quality_values})
        if self.fail:
            raise RuntimeError("capture must not fail conversion")
        return SimpleNamespace(status="stored", reason=None)


class FailingDocumentExtractor:
    def extract(self, **_kwargs) -> ExtractedDocument:
        raise InvalidFileContentError(
            "No recognizable transaction row pattern. missing_signals=transaction_row_pattern"
        )


def test_conversion_job_captures_preflight_flags() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    document = UploadedDocument.from_staged_upload(
        filename="statement.pdf",
        staged_upload=UploadedDocumentStage(
            path=staged_path,
            size_bytes=staged_path.stat().st_size,
            sha256_hex="abc123",
        ),
    )
    request = ConversionJob.create(
        document=ConversionDocumentReference.from_document(
            document,
            storage_key="doc_1234567890abcdef12345678",
        ),
        identity=IdentityContext(identity_type="user", identity_id="usr_123", quota_limit=10),
        scanned_likely=True,
        estimated_pages_count=4,
    )

    assert request.document.filename == "statement.pdf"
    assert request.preflight_result == DocumentPreflightResult(
        scanned_likely=True,
        estimated_pages_count=4,
    )


def test_document_conversion_runtime_tracks_ocr_progress_and_forwards_callback() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    observed_progress: list[tuple[int, int]] = []
    document = UploadedDocument.from_staged_upload(
        filename="statement.pdf",
        staged_upload=UploadedDocumentStage(
            path=staged_path,
            size_bytes=staged_path.stat().st_size,
            sha256_hex="abc123",
        ),
    )
    request = ConversionJob.create(
        document=ConversionDocumentReference.from_document(
            document,
            storage_key="doc_1234567890abcdef12345678",
        ),
        identity=IdentityContext(identity_type="user", identity_id="usr_123", quota_limit=10),
        scanned_likely=True,
        estimated_pages_count=3,
    )

    runtime = DocumentConversionRuntime.from_job(request)
    callback = runtime.build_ocr_progress_callback(
        job=request,
        hooks=ConversionExecutionHooks(
            on_ocr_progress=lambda current_page, total_page_count: observed_progress.append(
                (current_page, total_page_count)
            )
        ),
    )
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
    document_extractor = FakeDocumentExtractor()
    statement_parser = FakeStatementParser()
    pipeline = DocumentConversionPipeline(
        report_service=report_service,
        access_control_service=access_control_service,
        processing_pipeline=processing_pipeline,
        analysis_repository=analysis_repository,
        document_extractor=document_extractor,
        statement_parser=statement_parser,
    )

    response = pipeline.run(
        document=UploadedDocument.from_staged_upload(
            filename="statement.csv",
            staged_upload=UploadedDocumentStage(
                path=staged_path,
                size_bytes=staged_path.stat().st_size,
                sha256_hex="abc123",
            ),
        ),
        anonymous_fingerprint=None,
        user_token="user-token",
        authorization=None,
        access_cookie_token=None,
        scanned_likely=False,
        estimated_pages_count=None,
    )

    assert len(document_extractor.calls) == 1
    assert document_extractor.calls[0]["document"].filename == "statement.csv"
    assert len(statement_parser.calls) == 1
    assert len(processing_pipeline.calls) == 1
    assert processing_pipeline.calls[0]["document"].filename == "statement.csv"
    assert processing_pipeline.calls[0]["parsed_document"].file_type == "csv"
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


def test_non_clean_pdf_is_forwarded_to_best_effort_canonical_capture(caplog) -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    capture = RecordingCanonicalLayoutCapture()
    caplog.set_level(logging.INFO, logger="app.application.conversion.document_conversion_pipeline")
    pipeline = DocumentConversionPipeline(
        report_service=FakeReportService(),
        access_control_service=FakeAccessControlService(),
        processing_pipeline=FakeProcessingPipeline(),
        analysis_repository=FakeAnalysisRepository(),
        document_extractor=FakeDocumentExtractor(),
        statement_parser=FakeStatementParser(),
        canonical_layout_capture_service=capture,
    )

    response = pipeline.run(
        document=UploadedDocument.from_staged_upload(
            filename="statement.pdf",
            staged_upload=UploadedDocumentStage(
                path=staged_path,
                size_bytes=staged_path.stat().st_size,
                sha256_hex="abc123",
            ),
        ),
        anonymous_fingerprint=None,
        user_token="user-token",
        authorization=None,
        access_cookie_token=None,
        scanned_likely=False,
        estimated_pages_count=1,
    )

    assert response.status == ConversionPipelineStatus.COMPLETED
    assert len(capture.calls) == 1
    assert capture.calls[0]["document"].filename == "statement.pdf"
    assert capture.calls[0]["status"] == "Sucesso"
    assert capture.calls[0]["layout_name"] is None
    assert capture.calls[0]["selected_parser"] == "grouped"
    assert capture.calls[0]["bank_name"] == "Itau"
    assert capture.calls[0]["bank_code"] == "341"
    assert capture.calls[0]["page_texts"] == ("2026-06-18 PIX RECEBIDO 150,00",)
    assert capture.calls[0]["page_text_source"] == "parser"
    assert capture.calls[0]["source_layout_lines"][0][0].bbox["left"] == 0.12
    assert "identity" not in capture.calls[0]
    assert pipeline.access_control_service.recorded_user_conversions[-1]["canonical_capture_status"] == "stored"
    assert pipeline.access_control_service.recorded_user_conversions[-1]["canonical_capture_reason"] is None
    assert "canonical_layout_capture_result status=stored reason= storage=s3 sse=AES256" in caplog.text


def test_ocr_page_texts_are_forwarded_to_canonical_capture_with_ocr_source() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    capture = RecordingCanonicalLayoutCapture()
    pipeline = DocumentConversionPipeline(
        report_service=FakeReportService(),
        access_control_service=FakeAccessControlService(),
        processing_pipeline=FakeProcessingPipeline(),
        analysis_repository=FakeAnalysisRepository(),
        document_extractor=FakeOcrDocumentExtractor(),
        statement_parser=FakeStatementParser(),
        canonical_layout_capture_service=capture,
    )

    response = pipeline.run(
        document=UploadedDocument.from_staged_upload(
            filename="scanned-statement.pdf",
            staged_upload=UploadedDocumentStage(
                path=staged_path,
                size_bytes=staged_path.stat().st_size,
                sha256_hex="abc123",
            ),
        ),
        anonymous_fingerprint=None,
        user_token="user-token",
        authorization=None,
        access_cookie_token=None,
        scanned_likely=True,
        estimated_pages_count=1,
    )

    assert response.status == ConversionPipelineStatus.COMPLETED
    assert capture.calls[0]["page_texts"] == ("2026-06-18 PIX RECEBIDO 150,00",)
    assert capture.calls[0]["page_text_source"] == "ocr"


def test_free_inline_conversion_persists_canonical_capture_result() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    capture = RecordingCanonicalLayoutCapture()
    access_control_service = FakeAnonymousAccessControlService()
    pipeline = DocumentConversionPipeline(
        report_service=FakeReportService(),
        access_control_service=access_control_service,
        processing_pipeline=FakeProcessingPipeline(),
        analysis_repository=FakeAnalysisRepository(),
        document_extractor=FakeDocumentExtractor(),
        statement_parser=FakeStatementParser(),
        canonical_layout_capture_service=capture,
    )

    response = pipeline.run(
        document=UploadedDocument.from_staged_upload(
            filename="free-statement.pdf",
            staged_upload=UploadedDocumentStage(
                path=staged_path,
                size_bytes=staged_path.stat().st_size,
                sha256_hex="abc123",
            ),
        ),
        anonymous_fingerprint="free-browser",
        user_token=None,
        authorization=None,
        access_cookie_token=None,
        scanned_likely=False,
        estimated_pages_count=1,
    )

    assert response.status == ConversionPipelineStatus.COMPLETED
    assert len(capture.calls) == 1
    assert len(access_control_service.recorded_anonymous_conversions) == 2
    recorded = access_control_service.recorded_anonymous_conversions[-1]
    assert recorded["canonical_capture_status"] == "stored"
    assert recorded["canonical_capture_reason"] is None


def test_canonical_capture_exception_does_not_fail_conversion() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    access_control_service = FakeAccessControlService()
    pipeline = DocumentConversionPipeline(
        report_service=FakeReportService(),
        access_control_service=access_control_service,
        processing_pipeline=FakeProcessingPipeline(),
        analysis_repository=FakeAnalysisRepository(),
        document_extractor=FakeDocumentExtractor(),
        statement_parser=FakeStatementParser(),
        canonical_layout_capture_service=RecordingCanonicalLayoutCapture(fail=True),
    )

    response = pipeline.run(
        document=UploadedDocument.from_staged_upload(
            filename="statement.pdf",
            staged_upload=UploadedDocumentStage(
                path=staged_path,
                size_bytes=staged_path.stat().st_size,
                sha256_hex="abc123",
            ),
        ),
        anonymous_fingerprint=None,
        user_token="user-token",
        authorization=None,
        access_cookie_token=None,
        scanned_likely=False,
        estimated_pages_count=1,
    )

    assert response.status == ConversionPipelineStatus.COMPLETED
    assert access_control_service.recorded_user_conversions[-1]["canonical_capture_status"] == "boundary_failed"
    assert access_control_service.recorded_user_conversions[-1]["canonical_capture_reason"] == "RuntimeError"


def test_parser_failure_without_observability_is_forwarded_to_canonical_capture() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    capture = RecordingCanonicalLayoutCapture(failure_capture_enabled=True)
    access_control_service = FakeAccessControlService()
    pipeline = DocumentConversionPipeline(
        report_service=FakeReportService(),
        access_control_service=access_control_service,
        processing_pipeline=FakeProcessingPipeline(),
        analysis_repository=FakeAnalysisRepository(),
        document_extractor=FailingDocumentExtractor(),
        statement_parser=FakeStatementParser(),
        canonical_layout_capture_service=capture,
    )

    with pytest.raises(InvalidFileContentError, match="No recognizable transaction row pattern"):
        pipeline.run(
            document=UploadedDocument.from_staged_upload(
                filename="statement.pdf",
                staged_upload=UploadedDocumentStage(
                    path=staged_path,
                    size_bytes=staged_path.stat().st_size,
                    sha256_hex="abc123",
                ),
            ),
            anonymous_fingerprint=None,
            user_token="user-token",
            authorization=None,
            access_cookie_token=None,
            scanned_likely=False,
            estimated_pages_count=1,
        )

    assert len(capture.calls) == 1
    assert capture.calls[0]["status"] == "Falha"
    assert capture.calls[0]["transactions_count"] == 0
    assert capture.calls[0]["layout_name"] is None
    assert capture.calls[0]["selected_parser"] is None
    recorded = access_control_service.recorded_user_conversions[-1]
    assert recorded["canonical_capture_status"] == "stored"
    assert recorded["canonical_capture_reason"] is None


def test_scanned_ocr_page_limit_still_triggers_canonical_preview_capture() -> None:
    class PageLimitedExtractor:
        def extract(self, **_kwargs):
            raise MaxPagesPerFileExceededError(pages_count=24, max_pages_per_file=10)

    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    capture = RecordingCanonicalLayoutCapture(failure_capture_enabled=True)
    pipeline = DocumentConversionPipeline(
        report_service=FakeReportService(),
        access_control_service=FakeAccessControlService(),
        processing_pipeline=FakeProcessingPipeline(),
        analysis_repository=FakeAnalysisRepository(),
        document_extractor=PageLimitedExtractor(),
        statement_parser=FakeStatementParser(),
        canonical_layout_capture_service=capture,
    )

    with pytest.raises(MaxPagesPerFileExceededError):
        pipeline.run(
            document=UploadedDocument.from_staged_upload(
                filename="large-scan.pdf",
                staged_upload=UploadedDocumentStage(
                    path=staged_path, size_bytes=staged_path.stat().st_size, sha256_hex="abc123",
                ),
            ),
            anonymous_fingerprint=None,
            user_token="user-token",
            authorization=None,
            access_cookie_token=None,
            scanned_likely=True,
            estimated_pages_count=24,
        )

    assert len(capture.calls) == 1
    assert capture.calls[0]["status"] == "Falha"
    assert capture.calls[0]["transactions_count"] == 0


def test_async_job_materializes_missing_preflight_before_recording_conversion() -> None:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    document = UploadedDocument.from_staged_upload(
        filename="scanned-statement.pdf",
        staged_upload=UploadedDocumentStage(
            path=staged_path,
            size_bytes=staged_path.stat().st_size,
            sha256_hex="abc123",
        ),
    )
    access_control_service = FakeAccessControlService()
    preflight_service = RecordingDocumentPreflightService()
    pipeline = DocumentConversionPipeline(
        report_service=FakeReportService(),
        access_control_service=access_control_service,
        document_preflight_service=preflight_service,
        processing_pipeline=FakeProcessingPipeline(),
        analysis_repository=FakeAnalysisRepository(),
        document_extractor=FakeDocumentExtractor(),
        statement_parser=FakeStatementParser(),
    )
    job = ConversionJob.create(
        batch_id="batch_async_preflight",
        document=ConversionDocumentReference.from_document(
            document,
            storage_key="doc_1234567890abcdef12345678",
        ),
        identity=access_control_service.identity,
    )

    response = pipeline.run_job(job=job, document=document, hooks=ConversionExecutionHooks())

    assert job.preflight_result == DocumentPreflightResult(scanned_likely=None, estimated_pages_count=None)
    assert preflight_service.inspections == [("scanned-statement.pdf", document.raw_bytes)]
    assert access_control_service.recorded_user_conversions[-1]["scanned_likely"] is True
    assert response.metadata is not None
    assert response.metadata["is_scanned"] is True
