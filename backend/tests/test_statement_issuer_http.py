"""Issuer identity reaches public and worker OFX exports without changing the grid."""
import pytest

from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_santander_ocr_controls import santander_extraction
from tests.test_statement_issuer_evidence import issuer_pdf

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("title,holder,bank,code", [
    ("Banco Itau", "Razao Social: SANTANDER COMERCIO LTDA", "Itau", "341"),
    ("Banco Itau", "Nome/Razão Social CPF/CNPJ\nSANTANDER COMERCIO LTDA", "Itau", "341"),
    ("Banco Santander / Banco Bradesco", None, None, "000"),
])
def test_review_cases_preserve_financial_rows_and_export_the_right_identity(tmp_path, monkeypatch, title, holder, bank, code):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictional-issuer-review"}
        response = client.post("/convert", data=owner,
            files={"file": ("fictional.pdf", issuer_pdf(title=title, owner=holder), "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["bank_name"] == bank
        assert analysis["bank_code"] == (None if code == "000" else code)
        assert [row["amount"] for row in analysis["preview_transactions"]] == [-10, 50, -20]
        assert (analysis["opening_balance"], analysis["closing_balance"]) == (100, 120)
        ofx = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": "ofx"})
        assert ofx.status_code == 200 and f"<BANKID>{code}" in ofx.text
        assert ofx.text.count("<STMTTRN>") == 3


@pytest.mark.parametrize("mode", ["text", "analysis"])
@pytest.mark.parametrize("conflict", [False, True])
def test_ocr_review_cases_distinguish_owner_values_from_merged_issuers(tmp_path, monkeypatch, mode, conflict):
    from dataclasses import replace

    from app.application.document_extraction_models import ExtractedLine

    extraction = santander_extraction()
    if conflict:
        extraction = replace(extraction, pages=[replace(page, lines=[
            replace(line, text="Banco Santander / Banco Bradesco") if line.text == "Santander Empresas" else line
            for line in page.lines]) for page in extraction.pages])
    else:
        page = extraction.pages[0]
        holder = [ExtractedLine(id=f"holder-{i}", page_number=1, line_index=20+i, text=text,
            bbox={"left": .1, "top": .26+i*.02, "width": .5, "height": .008})
            for i, text in enumerate(["Nome/Razão Social CPF/CNPJ", "ITAU COMERCIO LTDA"])]
        extraction = replace(extraction, pages=[replace(page, lines=page.lines+holder), *extraction.pages[1:]])
    raw = configure_ocr(monkeypatch, extraction, mode)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictional-ocr-issuer-review"}
        response = client.post("/convert", data=owner, files={"file": ("fictional.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["bank_name"] == (None if conflict else "Santander")
        assert analysis["bank_code"] == (None if conflict else "033")
        assert [row["amount"] for row in analysis["preview_transactions"]] == [-100, -.16, 100.16, 1234.56, -1234.56, 300, -300]
        ofx = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": "ofx"})
        assert ofx.status_code == 200
        assert f"<BANKID>{'000' if conflict else '033'}" in ofx.text
        assert ofx.text.count("<STMTTRN>") == 7


@pytest.mark.parametrize("footer", [False, True])
def test_public_conversion_exports_the_explicit_issuer(tmp_path, monkeypatch, footer):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictional-issuer"}
        response = client.post("/convert", data=owner,
            files={"file": ("fictional.pdf", issuer_pdf(footer=footer), "application/pdf")})
        assert response.status_code == 200, response.text
        payload = response.json()
        analysis = payload["analysis"]
        assert analysis["bank_name"] == "Santander"
        assert analysis["transactions_total"] == 3
        assert analysis["layout_inference_name"] == "account_detail_descending_cash_v1"
        assert (analysis["opening_balance"], analysis["closing_balance"]) == (100, 120)
        ofx = client.get(f"/convert-report/{payload['processing_id']}", params={**owner, "format": "ofx"})
        assert ofx.status_code == 200, ofx.text
        assert "<BANKID>033" in ofx.text
        assert ofx.text.count("<STMTTRN>") == 3
        for amount in (-10, 50, -20):
            assert f"<TRNAMT>{amount:.2f}" in ofx.text
        assert client.get(f"/report/{payload['processing_id']}", params=owner).status_code == 200
        assert client.get("/report/an_issuer_nonexistent", params=owner).status_code == 404


def test_worker_preserves_footer_identity_in_stored_report(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with worker_case(tmp_path, issuer_pdf(footer=True)) as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["bank_name"] == "Santander"
        ofx = client.get(f"/convert-report/{analysis['analysis_id']}", params={"format": "ofx"})
        assert ofx.status_code == 200, ofx.text
        assert "<BANKID>033" in ofx.text
        assert ofx.text.count("<STMTTRN>") == 3


@pytest.mark.parametrize("mode", ["text", "analysis"])
def test_ocr_header_identity_reaches_the_export(tmp_path, monkeypatch, mode):
    raw = configure_ocr(monkeypatch, santander_extraction(), mode)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictional-ocr-issuer"}
        response = client.post("/convert", data=owner,
            files={"file": ("fictional.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["bank_name"] == "Santander"
        assert analysis["transactions_total"] == 7
        ofx = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": "ofx"})
        assert ofx.status_code == 200, ofx.text
        assert "<BANKID>033" in ofx.text
        assert ofx.text.count("<STMTTRN>") == 7
