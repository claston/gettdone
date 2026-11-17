from dataclasses import replace

import pytest

from app.application.document_extraction_models import RawDocumentExtraction
from app.application.errors import OcrFinancialAmbiguityError, UnsupportedDocumentContentError
from app.application.normalization.canonical import from_normalized_transaction
from app.application.pdf_parser import _original_visual_provenance
from app.workers.conversion_lambda import ConversionLambdaProcessor, handle_sqs_event
from tests.test_conversion_lambda_worker import FakeDocumentStore, _event, _queued_repository
from tests.test_embedded_ocr_quality import visual_result


def uncovered_result():
    result = visual_result()
    row = replace(from_normalized_transaction(result.transactions[0]), source_page=1)
    return replace(result, canonical_transactions=[row], source_page_texts=(
        "03/08/2026 PIX 100,00", "04/08/2026 PIX 20,00\n05/08/2026 PIX 30,00",
        "06/08/2026 PIX 40,00\n07/08/2026 PIX 50,00",
    ), source_extraction=RawDocumentExtraction("aws_textract", "visual", [], {}))


def test_missing_pages_are_named_and_ocr_evidence_survives_failure():
    with pytest.raises(OcrFinancialAmbiguityError) as caught:
        _original_visual_provenance(uncovered_result(), b"original", b"visual")
    error = caught.value
    assert "2, 3" in str(error)
    assert error._parse_observability["unextracted_pages"] == "2,3"
    assert error._ocr_source_extraction.provider == "aws_textract"
    assert error._ocr_source_extraction.document_hash != "visual"


def test_known_separate_annex_is_not_reported_as_missing_bank_movements():
    result = uncovered_result()
    result = replace(result, document_parts=[{
        "document_type": "fiscal_invoice", "pages": [2, 3], "included_in_statement": False, "requires_review": False,
    }])
    assert len(_original_visual_provenance(result, b"original", b"visual").transactions) == 1


def test_unknown_excluded_page_still_requires_coverage():
    result = replace(uncovered_result(), document_parts=[{
        "document_type": "unknown", "pages": [2, 3], "included_in_statement": False,
    }])
    with pytest.raises(OcrFinancialAmbiguityError):
        _original_visual_provenance(result, b"original", b"visual")


def test_worker_identifies_fiscal_note_without_retrying_it_as_a_statement():
    class FiscalPipeline:
        def run_job(self, **kwargs):
            raise UnsupportedDocumentContentError(document_type="fiscal_invoice", message="Nota fiscal identificada.")

    repository, job = _queued_repository()
    processor = ConversionLambdaProcessor(repository=repository, document_store=FakeDocumentStore(), pipeline=FiscalPipeline())
    result = handle_sqs_event(_event(job.job_id), processor=processor, request_id="fiscal-test")
    assert result["batchItemFailures"] == []
    assert repository.get(job.job_id).failure.code == "fiscal_invoice"
