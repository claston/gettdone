"""Failure telemetry must reuse the OCR evidence already paid for."""

import pytest

from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.uploaded_document import UploadedDocument
from app.application.document_type_classifier import DocumentTypeClassification
from app.application.errors import PdfFinancialValidationError
from tests.test_document_conversion_pipeline import (
    FakeAccessControlService,
    FakeAnalysisRepository,
    FakeAnonymousAccessControlService,
    FakeProcessingPipeline,
    FakeReportService,
    FakeStatementParser,
    RecordingAIRecoveryDispatcher,
)
from tests.test_recovery_case_ocr import bb_source


@pytest.mark.parametrize("anonymous", [False, True])
@pytest.mark.parametrize("initial_type", [None, "unknown", "fiscal_invoice"])
def test_failure_uses_ocr_classification_for_both_telemetry_and_recovery(anonymous, initial_type):
    source = bb_source()

    class Extractor:
        def extract(self, **kwargs):
            error = PdfFinancialValidationError("Saldos divergentes no extrato.")
            error._ocr_source_extraction = source
            error._parse_observability = {"layout_inference_name": "bb_statement_ptbr", "layout_inference_confidence": .99}
            if initial_type:
                error._document_type_classification = DocumentTypeClassification(initial_type, .99 if initial_type != "unknown" else 0)
            raise error

    service = FakeAnonymousAccessControlService() if anonymous else FakeAccessControlService()
    dispatcher = RecordingAIRecoveryDispatcher()
    pipeline = DocumentConversionPipeline(
        report_service=FakeReportService(), access_control_service=service,
        processing_pipeline=FakeProcessingPipeline(), analysis_repository=FakeAnalysisRepository(),
        document_extractor=Extractor(), statement_parser=FakeStatementParser(), ai_recovery_dispatcher=dispatcher,
    )
    with pytest.raises(PdfFinancialValidationError):
        pipeline.run(
            document=UploadedDocument(filename="scan.pdf", file_type="pdf", raw_bytes=b"synthetic scanned pdf"),
            anonymous_fingerprint="free-browser" if anonymous else None,
            user_token=None if anonymous else "user-token", authorization=None, access_cookie_token=None,
            scanned_likely=True, estimated_pages_count=1,
        )
    events = service.recorded_anonymous_conversions if anonymous else service.recorded_user_conversions
    expected = "fiscal_invoice" if initial_type == "fiscal_invoice" else "bank_statement"
    assert events[-1]["document_type"] == expected
    assert dispatcher.failure_calls[-1]["document_classification"].document_type == expected
    assert dispatcher.failure_calls[-1]["source_extraction"] is source
