from io import BytesIO

import pytest
from openpyxl import load_workbook

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.afternoon_statements import (
    alelo_page,
    btg_investment_page,
    btg_invoice_pages,
    c6_inactive_page,
    colmeia_pages,
    cresol_descending_pages,
    inter_page,
    unicred_page,
)
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_oct20_new_native_statements import bb_page, xp_page

pytestmark = pytest.mark.real_http


def native(pages):
    return generate_native_text_pdf(tuple(tuple(p.splitlines()) for p in pages))


CASES = [
    ("inter", [inter_page()], 3, "077", 722.1, "bank_statement"),
    ("cresol", cresol_descending_pages(), 4, "133", -30, "bank_statement"),
    ("colmeia", colmeia_pages(), 4, "133", 1, "bank_statement"),
    ("btg-investment", [btg_investment_page()], 2, "208", 0, "bank_statement"),
    ("btg-card", btg_invoice_pages(), 3, "208", -125, "credit_card_statement"),
    ("c6", [c6_inactive_page()], 0, "336", None, "bank_statement"),
    ("unicred", [unicred_page()], 2, "136", 120, "bank_statement"),
    ("alelo", [alelo_page()], 3, None, None, "prepaid_card_statement"),
    ("bb", [bb_page() + "\nInformações Adicionais\nLançamentos Futuros\n00/00/0000 0,00 (+)"], 2, "001", 1905.43, "bank_statement"),
    ("xp", [xp_page().replace("09:12:34", "09:   1:34")], 2, "348", 1200, "bank_statement"),
]


@pytest.mark.parametrize("kind,pages,count,bank,closing,document_type", CASES)
def test_recovery_variants_convert_download_and_keep_financial_identity(
    kind, pages, count, bank, closing, document_type, tmp_path, monkeypatch,
):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-afternoon-" + kind}
        response = client.post("/convert", data=owner, files={"file": (kind + ".pdf", native(pages), "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == count
        assert analysis["bank_code"] == bank
        assert analysis["closing_balance"] == closing
        assert analysis["document_type"] == document_type
        assert analysis["pdf_processing_metrics"]["textract_used"] == 0
        aid = analysis["analysis_id"]
        assert client.get(f"/report/{aid}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{aid}", params={**owner, "format": fmt})
            assert report.status_code == 200, report.text
            if fmt == "ofx":
                assert report.text.count("<STMTTRN>") == count
                if kind == "btg-card":
                    assert "<CCSTMTRS>" in report.text
                    assert "<BALAMT>-125.00" in report.text
                else:
                    assert "<STMTRS>" in report.text
                if kind == "c6":
                    assert "<LEDGERBAL>" not in report.text
            elif fmt == "xlsx":
                book = load_workbook(BytesIO(report.content))
                assert book.active.max_row >= count + 1


def test_invalid_balances_and_notary_receipt_have_specific_errors_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-afternoon-negative"}
        bad = native([inter_page().replace("722,10", "722,11")])
        response = client.post("/convert", data=owner, files={"file": ("inter.pdf", bad, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] > 0
        assert "financial_validation_failed" in analysis["preview_transactions"][0]["warning_types"]
        receipt = native(["CARTÓRIO DE NOTAS E PROTESTO\nRECIBO DE CANCELAMENTO DE PROTESTO N 123\nTotal R$ 20,00"])
        response = client.post("/convert", data=owner, files={"file": ("recibo.pdf", receipt, "application/pdf")})
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "unsupported_document_type"
        assert response.json()["detail"]["document_type"] == "notary_receipt"
        assert "cartório" in response.json()["detail"]["message"]
        assert client.get("/report/an_afternoon_missing", params=owner).status_code == 404
        # /convert replaced the legacy /analyze route in production.
        assert client.post("/analyze").status_code == 405


@pytest.mark.parametrize("pages,count,closing", [(colmeia_pages(), 4, 1), (btg_invoice_pages(), 3, -125)])
def test_async_worker_uses_the_same_validated_native_readers(pages, count, closing, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with worker_case(tmp_path, native(pages), filename="afternoon.pdf") as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == count
        assert analysis["closing_balance"] == closing
        assert client.get(f"/convert-report/{analysis['analysis_id']}", params={"format": "xlsx"}).status_code == 200
