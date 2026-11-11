"""Synthetic live HTTP checks for the three priority recovery fixes."""
import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.ocr_conversion_http_helpers import public_client
from tests.test_nov10_priority_recovery import bb_paired_dates_cells, c6_negative_balance, itau_same_page_summary, wise_variant

pytestmark = pytest.mark.real_http


def test_priority_readers_keep_movements_and_exports_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    wise = wise_variant(date_first=True, pipes=False)
    c6 = c6_negative_balance("-R$ 21,00")  # Readable control with a genuine mismatch.
    cases = [
        ("input.csv", b"date,description,amount\n2026-10-01,Entrada,100\n", [100]),
        ("bb.pdf", grid_pdf([bb_paired_dates_cells()]), [-20, 20]),
        ("itau.pdf", grid_pdf(itau_same_page_summary()), [-15, -5, 20, 20]),
        ("wise.pdf", generate_native_text_pdf((tuple(wise),)), [40, -40, 900, 450, 30, -1378.5]),
        ("c6.pdf", generate_native_text_pdf((tuple(c6.splitlines()),)), [100, -120, 50, -10]),
    ]
    with public_client(tmp_path) as client:
        for name, raw, amounts in cases:
            owner = {"anonymous_fingerprint": "synthetic-priority-nov10-"+name}
            response = client.post("/convert", data=owner, files={"file": (name, raw,
                "text/csv" if name.endswith(".csv") else "application/pdf")})
            assert response.status_code == 200, response.text
            analysis = response.json()["analysis"]
            assert analysis["transactions_total"] == len(amounts)
            assert [row["amount"] for row in analysis["preview_transactions"]] == amounts
            if name == "c6.pdf":
                assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
            ident = analysis["analysis_id"]
            assert client.get("/report/"+ident, params=owner).status_code == 200
            for fmt in ("csv", "xlsx", "ofx"):
                exported = client.get("/convert-report/"+ident, params={**owner, "format": fmt})
                assert exported.status_code == 200, exported.text
                if fmt == "ofx":
                    assert exported.text.count("<STMTTRN>") == len(amounts)
        bad = [line for line in wise if line != "-1.378,50 1,50"]
        rejected = client.post("/convert", data=owner, files={"file": (
            "damaged.pdf", generate_native_text_pdf((tuple(bad),)), "application/pdf")})
        assert rejected.status_code in {400, 422}, rejected.text
        assert client.get("/report/an_nov10_missing", params=owner).status_code == 404
        # The supported public route is /convert; /analyze remains disabled.
        assert client.post("/analyze").status_code == 405
