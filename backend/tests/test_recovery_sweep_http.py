"""Synthetic recovery reports over real HTTP and the production worker adapter."""

import pytest

from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_recovery_sweep_ocr import infinitepay, pagbank

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("mode", ["text", "tables"])
def test_pagbank_financial_contract_and_downloads_over_real_http(tmp_path, monkeypatch, mode):
    raw = configure_ocr(monkeypatch, pagbank(), mode)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-pagbank-synthetic"}
        response = client.post("/convert", data=owner, files={"file": ("pagbank.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        document = analysis["financial_document"]
        assert document["origin"] == "PagBank"
        assert len(document["events"]) == 4
        assert document["export"]["available_formats"] == ["csv", "xlsx"]
        identifier = analysis["analysis_id"]
        assert client.get(f"/report/{identifier}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx"):
            report = client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt})
            assert report.status_code == 200, report.text
        blocked = client.get(f"/convert-report/{identifier}", params={**owner, "format": "ofx"})
        assert blocked.status_code == 400, blocked.text
        assert client.get("/report/an_recovery_missing", params=owner).status_code == 404


def test_infinitepay_worker_preserves_partial_statement_warning(tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, infinitepay(), "tables")
    with worker_case(tmp_path, raw, filename="infinitepay-trecho.pdf") as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == 2
        assert analysis["pdf_processing_metrics"]["partial_statement"] == 1
        assert analysis["pdf_processing_metrics"]["financial_controls_validated"] == 0
        assert analysis["bank_name"] == "InfinitePay"
        report = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "csv"})
        assert report.status_code == 200, report.text
