"""Real HTTP conversion, financial contracts, exports and negative paths."""

from io import BytesIO

import pytest
from openpyxl import load_workbook

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_oct28_reviewed_reports import bradesco_unified_source, omie_pages

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind,count", [("omie", 3), ("bradesco", 4)])
def test_reviewed_financial_reports_convert_over_http(kind, count, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = (
        generate_native_text_pdf([omie_pages()[0].splitlines()])
        if kind == "omie"
        else configure_ocr(monkeypatch, bradesco_unified_source(savings=True))
    )
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-review-reports-" + kind}
        response = client.post("/convert", data=owner, files={"file": (kind + ".pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        data = response.json()["analysis"]
        assert data["transactions_total"] == count
        assert data["financial_document"]["document_type"] == "cash_flow_report"
        assert all(v["status"] == "passed" for v in data["financial_document"]["validations"])
        if kind == "omie":
            assert data["total_inflows"] == 50 and data["total_outflows"] == -20
        identifier = data["analysis_id"]
        assert client.get(f"/report/{identifier}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx"):
            exported = client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt})
            assert exported.status_code == 200, exported.text
            if fmt == "xlsx":
                book = load_workbook(BytesIO(exported.content), read_only=True, data_only=True)
                assert book.worksheets
                book.close()
        ofx = client.get(
            f"/convert-report/{identifier}",
            params={**owner, "format": "ofx", "bank_code": "237", "bank_branch": "1234", "account_number": "56789"},
        )
        assert ofx.status_code == (200 if kind == "omie" else 400), ofx.text
        if kind == "omie":
            assert ofx.text.count("<STMTTRN>") == 2
            assert "<TRNAMT>-20.00" in ofx.text
        assert client.get("/report/an_review_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


def test_worker_preserves_omie_pending_events_without_adding_them_to_cash(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf([omie_pages()[0].splitlines()])
    with worker_case(tmp_path, raw) as (client, job):
        data = job["result"]["analysis"]
        assert data["transactions_total"] == 3
        assert data["net_total"] == 30
        assert [e["cash_effect"] for e in data["financial_document"]["events"]] == ["50.00", None, "-20.00"]
