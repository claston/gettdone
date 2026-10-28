"""One delivery covers the audited native extraction variants through real HTTP."""

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_oct20_new_native_statements import bb_page
from tests.test_oct27_mercado_grid import mercado_grid
from tests.test_oct28_remaining_statements import bb_zero_with_blocked, sicoob_descending

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["mercado", "sicoob", "bb_pending", "bb_prior_balance"])
def test_native_remaining_cases_keep_transactions_reports_and_exports(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    if kind == "mercado":
        raw, amounts = mercado_grid(), [10, -5]
    elif kind == "sicoob":
        raw, amounts = sicoob_descending(), [50, -200]
    else:
        text = bb_zero_with_blocked() if kind == "bb_pending" else bb_page().replace("Saldo Anterior", "Saldo do dia")
        raw = generate_native_text_pdf((text.splitlines(),))
        amounts = [50] if kind == "bb_pending" else [-194.57, 1100]
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-oct28-remaining-"+kind}
        response = client.post("/convert", data=owner, files={"file": (kind+".pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        data = response.json()["analysis"]
        assert [row["amount"] for row in data["preview_transactions"]] == amounts
        identifier = data["analysis_id"]
        assert client.get(f"/report/{identifier}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt})
            assert report.status_code == 200, report.text
            if fmt == "ofx":
                assert report.text.count("<STMTTRN>") == len(amounts)
        assert client.get("/report/an_oct28_remaining_nonexistent", params=owner).status_code == 404


def test_sicoob_worker_uses_the_same_descending_ledger_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with worker_case(tmp_path, sicoob_descending()) as (client, job):
        data = job["result"]["analysis"]
        assert [row["amount"] for row in data["preview_transactions"]] == [50, -200]
        assert data["pdf_processing_metrics"]["balance_consistency_failed"] == 0
        assert client.get(f"/convert-report/{data['analysis_id']}", params={"format": "ofx"}).status_code == 200


def test_csv_and_disabled_analyze_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-oct28-remaining-csv"}
        response = client.post("/convert", data=owner, files={"file": ("test.csv",
            b"date,description,amount\n2026-10-28,Recebimento teste,100\n", "text/csv")})
        assert response.status_code == 200, response.text
        assert client.get(f"/report/{response.json()['analysis']['analysis_id']}", params=owner).status_code == 200
        assert client.post("/analyze").status_code == 405
