"""Real HTTP/export acceptance for fictional recovered native and OCR statements."""
from decimal import Decimal

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import configure_ocr, public_client
from tests.test_followup_conversion_recovery import (
    btg_native,
    caixa_ocr_web,
    infinitepay_native,
    ndd_native,
    revolut_native,
    stone_empty_lines,
)

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("factory,amounts", [
    (ndd_native, [100, -30, -10]), (btg_native, [100, -30]),
    (infinitepay_native, [-20, 50, -10]), (revolut_native, [-20, 50, -10]),
    (lambda: generate_native_text_pdf((tuple(stone_empty_lines()),)), []),
])
def test_recovered_native_pdf_convert_report_and_ofx_use_the_same_movements(tmp_path, monkeypatch, factory, amounts):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictitious-followup-native"}
        response = client.post("/convert", data=owner, files={"file": ("ficticio.pdf", factory(), "application/pdf")})
        assert response.status_code == 200, response.text
        identifier = response.json()["processing_id"]
        report = response.json()["analysis"]
        response = client.get(f"/report/{identifier}", params=owner)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert report["transactions_total"] == len(amounts)
        assert report["pdf_processing_metrics"]["textract_used"] == 0
        if factory is btg_native:
            assert report["account_number"] == "00123456789-0"
        exported = client.get(f"/convert-report/{identifier}", params={**owner, "format": "ofx"})
        assert exported.status_code == 200, exported.text
        assert exported.text.count("<STMTTRN>") == len(amounts)
        if factory is btg_native:
            assert "<ACCTID>001234567890" in exported.text
        for amount in amounts:
            assert f"<TRNAMT>{Decimal(str(amount)):.2f}" in exported.text
        assert client.get("/report/an_followup_nonexistent", params=owner).status_code == 404


def test_conflicting_caixa_openings_are_explained_in_http_response_without_cash_adjustment(tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, caixa_ocr_web(ambiguous=True))
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictitious-followup-caixa"}
        response = client.post("/convert", data=owner, files={"file": ("ficticio.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == 4
        metrics = analysis["pdf_processing_metrics"]
        assert metrics["opening_balance_ambiguity_reason"] == "conflicting_printed_balances"
        assert metrics["financial_controls_validated"] == 0
        assert "opening_balance_ambiguous" in metrics["canonical_warning_types_list"]
        exported = client.get(f"/convert-report/{response.json()['processing_id']}", params={**owner, "format": "ofx"})
        assert exported.status_code == 200, exported.text
        assert exported.text.count("<STMTTRN>") == 4
        for amount in [10, -30, -2, 5]:
            assert f"<TRNAMT>{Decimal(str(amount)):.2f}" in exported.text
