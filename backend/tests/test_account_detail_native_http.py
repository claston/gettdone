"""Real public/worker HTTP flows preserve the account-detail column variants."""
import csv
from collections import Counter
from io import BytesIO, StringIO

import pytest
from openpyxl import load_workbook

from app.application.parsers.ofx import parse_ofx_transactions
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_account_detail_native_variants import account_detail_pdf

pytestmark = pytest.mark.real_http
EXPECTED = Counter([("2026-10-03", .12), ("2026-10-02", -10), ("2026-10-02", -10),
                    ("2026-10-02", -20), ("2026-10-01", 50)])


def assert_exports(client, analysis, owner):
    assert analysis["transactions_total"] == 5
    assert (analysis["opening_balance"], analysis["closing_balance"]) == (100, 110.12)
    assert analysis["bank_name"] is None
    assert analysis["pdf_processing_metrics"]["financial_controls_validated"] == 1
    assert analysis["pdf_processing_metrics"]["textract_used"] == 0
    assert Counter((r["date"], r["amount"]) for r in analysis["preview_transactions"]) == EXPECTED
    identifier = analysis["analysis_id"]
    for fmt in ("csv", "xlsx", "ofx"):
        response = client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt})
        assert response.status_code == 200, response.text
        if fmt == "csv":
            rows = csv.DictReader(line for line in StringIO(response.content.decode("utf-8-sig")) if line.strip())
            assert Counter((r["date"], float(r["amount"])) for r in rows) == EXPECTED
        elif fmt == "ofx":
            assert Counter((r.date, r.amount) for r in parse_ofx_transactions(response.content)) == EXPECTED
            assert "<BALAMT>110.12" in response.text
        else:
            book = load_workbook(BytesIO(response.content), read_only=True, data_only=True)
            amounts = [round(float(row[2] or 0) - float(row[3] or 0), 2)
                       for row in book["Conversao"].iter_rows(min_row=2, values_only=True)]
            assert Counter(amounts) == Counter(amount for _, amount in EXPECTED.elements())
            book.close()
    assert client.get("/report/an_detail_nonexistent", params=owner).status_code == 404


def test_public_conversion_and_download_preserve_native_column_variants(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictional-account-detail"}
        response = client.post("/convert", data=owner,
            files={"file": ("account.pdf", account_detail_pdf(), "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert client.get(f"/report/{analysis['analysis_id']}", params=owner).status_code == 200
        assert_exports(client, analysis, owner)


def test_worker_conversion_and_download_preserve_native_column_variants(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with worker_case(tmp_path, account_detail_pdf()) as (client, job):
        assert_exports(client, job["result"]["analysis"], {})
