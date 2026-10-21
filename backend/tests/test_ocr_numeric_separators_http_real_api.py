"""Real HTTP and worker regressions for the shared OCR separator rule."""

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import RawDocumentExtraction
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_inter_ocr_ledger import change, inter_extraction
from tests.test_itau_monthly_ocr_separators import _change_word, _page
from tests.test_nubank_ocr_daily_blocks import change_text, nubank_extraction

pytestmark = pytest.mark.real_http


def _configure_source(monkeypatch, *, wrong_balance=False):
    page = _page()
    if wrong_balance:
        _change_word(page, "balance-5", text="2,00")
    extraction = RawDocumentExtraction("aws_textract", "synthetic", [page], {})
    raw = configure_ocr(monkeypatch, extraction)
    response = pdf_parser.TextractGateway.analyze_pdf()
    response["blocks"].extend([
        {"Id": word.id, "BlockType": "WORD", "Page": 1, "Text": word.text, "TextType": word.text_type,
         "Confidence": word.confidence,
         "Geometry": {"BoundingBox": {key.title(): value for key, value in word.bbox.items()}}}
        for word in page.words
    ])
    calls = []

    def analyze(*args, **kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(pdf_parser.TextractGateway, "analyze_pdf", analyze)
    return raw, calls


def _assert_recovered(analysis):
    assert [row["amount"] for row in analysis["preview_transactions"]] == [-2200, -20000, 5697.36, 12000, 15000, -12697.36]
    metrics = analysis["pdf_processing_metrics"]
    assert metrics["ocr_unresolved_rows"] == 0
    assert metrics["balance_consistency_failed"] == 0
    assert metrics["canonical_warning_count"] == 0


def test_public_conversion_and_ofx_include_validated_separator_repair(tmp_path, monkeypatch):
    raw, calls = _configure_source(monkeypatch)
    owner = {"anonymous_fingerprint": "fp-itau-separator-repair"}
    with public_client(tmp_path) as client:
        result = client.post("/convert", data=owner, files={"file": ("itau.pdf", raw, "application/pdf")})
        assert result.status_code == 200, result.text
        analysis = result.json()["analysis"]
        _assert_recovered(analysis)
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        ofx = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": "ofx"})
        assert ofx.status_code == 200, ofx.text
        assert ofx.text.count("<STMTTRN>") == 6
        assert "<TRNAMT>-20000.00" in ofx.text
        assert client.get("/report/an_itau_separator_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405
    assert len(calls) == 1


def test_worker_uses_the_same_validated_separator_repair(tmp_path, monkeypatch):
    raw, calls = _configure_source(monkeypatch)
    with worker_case(tmp_path, raw, filename="itau.pdf") as (client, job):
        _assert_recovered(job["result"]["analysis"])
        ofx = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
        assert ofx.status_code == 200, ofx.text
        assert "<TRNAMT>-20000.00" in ofx.text
    assert len(calls) == 1


def test_inconsistent_cash_balances_still_require_review_without_ocr_retry(tmp_path, monkeypatch):
    raw, calls = _configure_source(monkeypatch, wrong_balance=True)
    with public_client(tmp_path) as client:
        result = client.post("/convert", data={"anonymous_fingerprint": "fp-itau-separator-unconfirmed"},
                             files={"file": ("itau.pdf", raw, "application/pdf")})
        assert result.status_code == 200, result.text
        analysis = result.json()["analysis"]
        assert len(analysis["preview_transactions"]) == 5
        metrics = analysis["pdf_processing_metrics"]
        assert metrics["ocr_unresolved_rows"] == 1
        assert metrics["balance_consistency_failed"] == 1
        assert metrics["export_recommendation"] == "review_recommended"
    assert len(calls) == 1


@pytest.mark.parametrize("bank", ["nubank", "inter"])
def test_public_conversion_uses_shared_separator_rule_for_other_banks(bank, tmp_path, monkeypatch):
    source = (change_text(nubank_extraction(), "1.100,00", "1,100,00") if bank == "nubank"
              else change(inter_extraction(), "R$ 1.478,85", "R$ 1,478,85"))
    expected = [1234.74, 1222.36, 1100, -400, -335.75] if bank == "nubank" else [-29.98, -50, 200, -100, -267.01]
    raw = configure_ocr(monkeypatch, source)
    with public_client(tmp_path) as client:
        result = client.post("/convert", data={"anonymous_fingerprint": "fp-shared-ocr-" + bank},
                             files={"file": (bank + ".pdf", raw, "application/pdf")})
        assert result.status_code == 200, result.text
        analysis = result.json()["analysis"]
        assert [row["amount"] for row in analysis["preview_transactions"]] == expected
        assert analysis["pdf_processing_metrics"]["balance_consistency_failed"] == 0
