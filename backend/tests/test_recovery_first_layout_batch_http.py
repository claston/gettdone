"""Real HTTP exports for the synthetic recovery batch, including review policy."""

import pytest

from tests.fixtures.positioned_cash_statements import positioned_pdf
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.ocr_conversion_http_helpers import public_client
from tests.test_pdf_parser_bradesco_card_web import native_pdf, statement_pages
from tests.test_recovery_first_layout_batch import CONTACT, banrisul_two_months, santander_basic_pages

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("case", ["banrisul", "banrisul_gap", "santander", "bradesco_card"])
def test_recovered_native_layouts_upload_report_and_export(case, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    if case.startswith("banrisul"):
        source = banrisul_two_months(month="JUL" if case.endswith("gap") else "MAI")
        raw = grid_pdf([[(line.text, line.bbox["left"], line.bbox["top"]) for line in source.pages[0].lines]])
        count = 4
    elif case == "santander":
        raw, count = positioned_pdf(santander_basic_pages()), 4
    else:
        pages = statement_pages()
        pages[0] = pages[0].replace("03/01 LOJA", CONTACT + "03/01 LOJA")
        raw, count = native_pdf(pages), 7
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-recovery-batch-" + case}
        response = client.post("/convert", data=owner, files={"file": ("synthetic.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        data = response.json()["analysis"]
        assert data["transactions_total"] == count
        assert client.get(f"/report/{data['analysis_id']}", params=owner).status_code == 200
        exported = client.get(f"/convert-report/{data['analysis_id']}", params={**owner, "format": "ofx"})
        assert exported.status_code == 200
        assert exported.text.count("<STMTTRN>") == count
        if case == "banrisul_gap":
            assert data["opening_balance"] is data["closing_balance"] is None
            assert "<LEDGERBAL>" not in exported.text
            assert data["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
        if case == "bradesco_card":
            assert data["document_type"] == "credit_card_statement"
            assert data["opening_balance"] is data["closing_balance"] is None
        assert client.get("/report/an_nonexistent_recovery_batch", params=owner).status_code == 404
