"""Recovery fixes through the public API, real HTTP and downloadable reports."""

import re
from dataclasses import replace

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import configure_ocr, public_client
from tests.test_recovery_case_fixes import mercado_pago_page, sicredi_pages
from tests.test_recovery_case_ocr import bb_source, uniprime_source

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["csv", "sicredi", "mercado_pago", "bb", "uniprime", "uniprime_tables"])
def test_recovery_ledgers_keep_all_cash_rows_through_normalization_and_exports(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    if kind in {"bb", "uniprime", "uniprime_tables"}:
        source = bb_source() if kind == "bb" else uniprime_source()
        if kind.startswith("uniprime"):
            page = source.pages[0]
            source = replace(source, pages=[replace(page, lines=[replace(line, text="20,00") if line.text == "20,00 -" else line
                                                                for line in page.lines])])
        raw = configure_ocr(monkeypatch, source, "tables" if kind.endswith("tables") else "text")
    elif kind == "csv":
        raw = b"date,description,amount\n2026-07-01,CREDITO,50\n2026-07-02,DEBITO,-20\n"
    else:
        pages = sicredi_pages() if kind == "sicredi" else [mercado_pago_page()]
        raw = generate_native_text_pdf(tuple(page.splitlines() for page in pages))
    filename, mime = ("input.csv", "text/csv") if kind == "csv" else ("input.pdf", "application/pdf")
    expected = {"csv": [50, -20], "sicredi": [10, 10, 10], "mercado_pago": [50, -20, 20], "bb": [-20],
                "uniprime": [50, -20], "uniprime_tables": [50, -20]}[kind]
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-recovery-cases"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert [row["amount"] for row in analysis["preview_transactions"]] == expected
        assert analysis["transactions_total"] == len(expected)
        if kind.startswith("uniprime"):
            assert analysis["bank_name"] == "Uniprime"
            assert analysis["bank_code"] == "099"
            assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
            assert "amount_sign_from_printed_balances" in analysis["preview_transactions"][-1]["warning_types"]
        analysis_id = analysis["analysis_id"]
        report = client.get(f"/report/{analysis_id}", params=owner)
        assert report.status_code == 200 and report.content.startswith(b"PK")
        ofx = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": "ofx"})
        assert ofx.status_code == 200 and ofx.text.count("<STMTTRN>") == len(expected)
        if kind == "mercado_pago":
            fitids = re.findall(r"<FITID>([^<\r\n]+)", ofx.text)
            assert len(fitids) == len(set(fitids)) == 3
        if kind.startswith("uniprime"):
            assert "<BANKID>099" in ofx.text
        assert client.get("/report/an_recovery_missing", params=owner).status_code == 404
        # The deployed application has retired this legacy route.
        assert client.post("/analyze", files={"file": (filename, raw, mime)}).status_code == 405


@pytest.mark.parametrize("kind", ["cnab", "missing_pages"])
def test_recovery_invalid_documents_have_actionable_errors_over_real_http(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    text = ("02RETORNO01COBRANCA" + "0" * 300 if kind == "cnab"
            else "Santander EXTRATO CONSOLIDADO INTELIGENTE\nPagina: 2/17")
    raw = generate_native_text_pdf((text.splitlines(),))
    with public_client(tmp_path) as client:
        response = client.post("/convert", data={"anonymous_fingerprint": "fp-recovery-invalid"},
                               files={"file": ("input.pdf", raw, "application/pdf")})
        assert response.status_code == 400, response.text
        if kind == "cnab":
            assert response.json()["detail"]["document_type"] == "bank_collection_return"
            assert "CNAB" in response.text
        else:
            assert "páginas" in response.text and "completo" in response.text
