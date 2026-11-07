"""Real public/worker HTTP keeps warnings and exports while explaining scope."""

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_postdeploy_recovery_cases import mercado_related_events

pytestmark = pytest.mark.real_http


def test_public_guidance_does_not_remove_reference_warnings_or_movements(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf((mercado_related_events(True).splitlines(),))
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-guidance"}
        response = client.post("/convert", data=owner, files={"file": ("statement.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["review_guidance"]["category"] == "information"
        assert [t["amount"] for t in analysis["preview_transactions"]] == [50, -50, 50]
        assert all("shared_operation_reference" in t["warning_types"] for t in analysis["preview_transactions"])
        assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
        assert client.get("/report/" + analysis["analysis_id"], params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get("/convert-report/" + analysis["analysis_id"], params={**owner, "format": fmt})
            assert report.status_code == 200
            if fmt == "ofx":
                assert report.text.count("<STMTTRN>") == 3
        response = client.post(
            "/convert", data=owner, files={"file": ("cash.csv", b"date,description,amount\n2026-10-01,Entrada,10\n", "text/csv")}
        )
        assert response.status_code == 200
        assert response.json()["analysis"]["review_guidance"] is None
        assert client.get("/report/an_missing_guidance", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


def test_worker_exposes_the_same_guidance_for_preserved_repeated_references(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf((mercado_related_events(True).splitlines(),))
    with worker_case(tmp_path, raw) as (_, job):
        analysis = job["result"]["analysis"]
        assert analysis["review_guidance"]["category"] == "information"
        assert analysis["transactions_total"] == 3
