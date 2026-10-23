from io import BytesIO

import pytest
from openpyxl import load_workbook

from tests.fixtures.positioned_cash_statements import bradesco_pages, change_cells, positioned_pdf, santander_pages
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_caixa_visa_invoice_ocr import caixa_invoice, modify

pytestmark = pytest.mark.real_http


def document(kind, monkeypatch):
    if kind == "caixa":
        return configure_ocr(monkeypatch, caixa_invoice()), 5, "104", -265
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    pages = santander_pages() if kind == "santander" else bradesco_pages(kind)
    return positioned_pdf(pages), 4 if kind == "santander" else 3, "033" if kind == "santander" else "237", 0 if kind == "santander" else 1


@pytest.mark.parametrize("kind", ["unified", "movements", "santander", "caixa", "csv"])
def test_corrected_cash_and_invoice_public_http_reports_all_exports_and_negative_paths(kind, tmp_path, monkeypatch):
    if kind == "csv":
        raw, count, bank, closing = b"date,description,amount\n2026-09-01,PIX RECEBIDO,100\n", 1, None, None
        filename, mime = "statement.csv", "text/csv"
    else:
        raw, count, bank, closing = document(kind, monkeypatch)
        filename, mime = kind+".pdf", "application/pdf"
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint":"fp-cash-invoice-"+kind}
        response = client.post("/convert", data=owner, files={"file":(filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == count
        if bank:
            assert analysis["bank_code"] == bank
            assert analysis["closing_balance"] == closing
            assert analysis["pdf_processing_metrics"]["balance_consistency_failed"] == 0
        if kind == "caixa":
            assert analysis["ofx_account_type"] == "credit_card"
            assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
            assert sum(bool(row["warning_types"]) for row in analysis["preview_transactions"]) == 1
        aid = analysis["analysis_id"]
        assert client.get(f"/report/{aid}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            exported = client.get(f"/convert-report/{aid}", params={**owner,"format":fmt})
            assert exported.status_code == 200, exported.text
            if fmt == "ofx":
                assert exported.text.count("<STMTTRN>") == count
                if kind == "caixa":
                    assert "<CCSTMTRS>" in exported.text and "<BALAMT>-265.00" in exported.text
                elif bank:
                    assert f"<BANKID>{bank}" in exported.text
            if fmt == "xlsx":
                assert load_workbook(BytesIO(exported.content))["Conversao"].max_row == count+1
        assert client.get("/report/an_missing_cash_invoice", params=owner).status_code == 404
        # The deployed API replaced /analyze with /convert; the legacy route stays disabled.
        assert client.post("/analyze").status_code == 405


@pytest.mark.parametrize("kind", ["unified", "santander", "caixa"])
def test_bad_financial_controls_convert_with_review_in_real_http(kind, tmp_path, monkeypatch):
    if kind == "caixa":
        raw = configure_ocr(monkeypatch, modify(caixa_invoice(), "5,00C", "5,00D"))
    else:
        monkeypatch.setenv("TEXTRACT_ENABLED", "false")
        monkeypatch.setenv("PDF_OCR_ENABLED", "false")
        pages = (change_cells(santander_pages(), "23,78", "23,79") if kind == "santander"
                 else change_cells(bradesco_pages(), "200,00", "201,00"))
        raw = positioned_pdf(pages)
    with public_client(tmp_path) as client:
        response = client.post("/convert", data={"anonymous_fingerprint":"fp-bad-cash-invoice"},
                               files={"file":(kind+".pdf",raw,"application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] > 0
        assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
        assert any("financial_validation_failed" in row["warning_types"] for row in analysis["preview_transactions"])


@pytest.mark.parametrize("kind", ["unified", "santander", "caixa"])
def test_worker_uses_same_cash_and_invoice_parsers_and_exports(kind, tmp_path, monkeypatch):
    raw, count, bank, closing = document(kind, monkeypatch)
    with worker_case(tmp_path, raw, filename=kind+".pdf") as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == count and analysis["closing_balance"] == closing
        assert analysis["bank_code"] == bank
        exported = client.get(f"/convert-report/{analysis['analysis_id']}", params={"format":"ofx"})
        assert exported.status_code == 200 and exported.text.count("<STMTTRN>") == count
        if kind == "caixa":
            assert "<CCSTMTRS>" in exported.text
            assert sum(bool(row["warning_types"]) for row in analysis["preview_transactions"]) == 1
