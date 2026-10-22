"""Public uploads and exports for synthetic structures from the final sweep."""
from dataclasses import replace

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import configure_ocr, public_client
from tests.test_final_recovery_native import brb_page, english_statement
from tests.test_final_recovery_ocr import bb_combined, caixa_page, sicredi
from tests.test_final_recovery_sales import sales_report

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind,count,code", [("bb", 2, "001"), ("brb", 2, "070"), ("sales", 2, None)])
def test_new_native_structures_over_http_and_exports(kind, count, code, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    text = {"bb": english_statement, "brb": brb_page, "sales": sales_report}[kind]().replace("−", "-")
    raw = generate_native_text_pdf((tuple(text.splitlines()),))
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-final-native-"+kind}
        response = client.post("/convert", data=owner, files={"file": (kind+".pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        data = response.json()["analysis"]
        assert data["transactions_total"] == count
        if code:
            assert data["bank_code"] == code
        else:
            assert data["financial_document"]["document_type"] == "acquirer_sales_report"
            assert all(e["cash_effect"] is None for e in data["financial_document"]["events"])
        identifier = data["analysis_id"]
        assert client.get(f"/report/{identifier}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            exported = client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt})
            assert exported.status_code == (400 if kind == "sales" and fmt == "ofx" else 200), exported.text
            if kind == "bb" and fmt == "ofx":
                assert "<DTPOSTED>20261013" in exported.text
                assert exported.text.count("<STMTTRN>") == 2


@pytest.mark.parametrize("kind", ["bb", "sicredi", "caixa"])
@pytest.mark.parametrize("mode", ["text", "tables"])
def test_final_ocr_structures_over_http_preserve_controls_and_review(kind, mode, tmp_path, monkeypatch):
    if kind == "caixa":
        source = replace(sicredi(), pages=[caixa_page("01/01/2024", "31/01/2024", "02/01/2024", "100.00", "150.00")])
    else:
        source = {"bb": bb_combined, "sicredi": sicredi}[kind]()
    raw = configure_ocr(monkeypatch, source, mode)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-final-ocr-"+kind}
        response = client.post("/convert", data=owner, files={"file": (kind+".pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        data = response.json()["analysis"]
        assert data["transactions_total"] == (2 if kind == "sicredi" else 1)
        assert data["pdf_processing_metrics"]["financial_controls_validated"] == int(kind == "sicredi")
        assert data["pdf_processing_metrics"]["balance_consistency_failed"] == 0
        if kind != "sicredi":
            assert data["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"


def test_csv_compatibility_missing_report_and_invalid_native_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-final-compatibility"}
        raw = b"date,description,amount\n2026-10-01,Entrada teste,100\n2026-10-02,Saida teste,-20\n"
        response = client.post("/convert", data=owner, files={"file": ("teste.csv", raw, "text/csv")})
        assert response.status_code == 200, response.text
        identifier = response.json()["analysis"]["analysis_id"]
        assert client.get(f"/report/{identifier}", params=owner).status_code == 200
        assert client.get("/report/an_final_missing", params=owner).status_code == 404
        invalid = english_statement().replace("2 transactions", "3 transactions")
        raw = generate_native_text_pdf((tuple(invalid.splitlines()),))
        assert client.post("/convert", data=owner, files={"file": ("invalido.pdf", raw, "application/pdf")}).status_code == 400
        assert client.post("/analyze").status_code == 405
