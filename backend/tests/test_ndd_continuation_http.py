"""Real HTTP acceptance for readable NDD pages without repeated column titles."""

import pytest

from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_ndd_continuation import ndd_continuation

pytestmark = pytest.mark.real_http


def test_ndd_public_conversion_report_and_exports_preserve_every_page(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictitious-ndd-continuation"}
        response = client.post("/convert", data=owner, files={
            "file": ("ndd-ficticio.pdf", ndd_continuation(), "application/pdf"),
        })
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        identifier = analysis["analysis_id"]
        assert analysis["transactions_total"] == 3
        assert analysis["layout_inference_name"] == "ndd_conta_digital_operacoes_v1"
        assert client.get(f"/report/{identifier}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            exported = client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt})
            assert exported.status_code == 200, exported.text
            if fmt == "ofx":
                assert exported.text.count("<STMTTRN>") == 3
                for amount in ("100.00", "-30.00", "-10.00"):
                    assert f"<TRNAMT>{amount}" in exported.text
        assert client.get("/report/an_ndd_nonexistent", params=owner).status_code == 404
        # /analyze is intentionally disabled in the deployed API.
        assert client.post("/analyze").status_code == 405
        analyzed = client.post("/convert", data=owner, files={
            "file": ("ficticio.csv", b"date,description,amount\n2026-10-01,PIX,100.00\n", "text/csv"),
        })
        assert analyzed.status_code == 200, analyzed.text


def test_ndd_worker_completes_the_same_continuation(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with worker_case(tmp_path, ndd_continuation(), filename="ndd-ficticio.pdf") as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == 3
        assert analysis["pdf_processing_metrics"]["financial_controls_validated"] == 1
        exported = client.get(f"/convert-report/{analysis['analysis_id']}", params={"format": "ofx"})
        assert exported.status_code == 200, exported.text
        assert exported.text.count("<STMTTRN>") == 3
