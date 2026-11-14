"""Real HTTP preserves recovered movements, review and downloadable reports."""
import pytest

from tests.fixtures.recovery_acceptance import grid_pdf
from tests.ocr_conversion_http_helpers import public_client
from tests.test_nov14_recovery_boundaries import itau_without_summary, pagbank_single_day, sicoob_opening_controls

pytestmark = pytest.mark.real_http


def test_recovered_cash_and_review_survive_real_http(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    cases = [
        ("input.csv", b"date,description,amount\n2026-08-03,Entrada,100\n", [100], None),
        ("pagbank.pdf", grid_pdf(pagbank_single_day()), [-20], "opening_inferred_from_closing"),
        ("sicoob.pdf", grid_pdf(sicoob_opening_controls()), [50, -10, -10], None),
        ("sicoob-review.pdf", grid_pdf(sicoob_opening_controls(mismatch=True)), [50, -10, -10], "financial_validation_failed"),
        ("itau.pdf", grid_pdf(itau_without_summary()), [20, 20, -5, -15], "partial_statement"),
    ]
    with public_client(tmp_path) as client:
        for name, raw, expected, reason in cases:
            owner = {"anonymous_fingerprint": "synthetic-nov14-" + name}
            response = client.post("/convert", data=owner, files={"file": (
                name, raw, "text/csv" if name.endswith(".csv") else "application/pdf")})
            assert response.status_code == 200, response.text
            analysis = response.json()["analysis"]
            assert [r["amount"] for r in analysis["preview_transactions"]] == expected
            if reason:
                metrics = analysis["pdf_processing_metrics"]
                assert metrics["export_recommendation_reason"] == reason
                assert metrics["financial_controls_validated"] == 0
            ident = analysis["analysis_id"]
            assert client.get("/report/" + ident, params=owner).status_code == 200
            for fmt in ("csv", "xlsx", "ofx"):
                exported = client.get("/convert-report/" + ident, params={**owner, "format": fmt})
                assert exported.status_code == 200, exported.text
                if fmt == "ofx":
                    assert exported.text.count("<STMTTRN>") == len(expected)
        damaged = client.post("/convert", data=owner, files={"file": (
            "damaged.pdf", grid_pdf(pagbank_single_day(amount="-R$ 2?,00")), "application/pdf")})
        assert damaged.status_code in {400, 422}, damaged.text
        assert client.get("/report/an_nov14_missing", params=owner).status_code == 404
        # /analyze is intentionally disabled in the deployed public API.
        assert client.post("/analyze").status_code == 405
