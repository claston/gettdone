"""Real HTTP coverage for period review, stored reports and compatible exports."""

import json

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import configure_ocr, public_client
from tests.test_oct27_today_ocr_controls import mercado
from tests.test_optional_document_period import _without_period
from tests.test_recovery_case_fixes import mercado_pago_page

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("mode", ["native", "text", "tables"])
def test_period_review_survives_http_reports_and_exports(mode, tmp_path, monkeypatch):
    if mode == "native":
        monkeypatch.setenv("TEXTRACT_ENABLED", "false")
        monkeypatch.setenv("PDF_OCR_ENABLED", "false")
        text = mercado_pago_page().replace("Periodo: De 01-07-2026 al 31-07-2026\n", "")
        raw = generate_native_text_pdf((text.splitlines(),))
        amounts = [50, -20, 20]
    else:
        raw = configure_ocr(monkeypatch, _without_period(mercado(annotated=False)), mode)
        amounts = [50, -20]
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-period-review-" + mode}
        response = client.post("/convert", data=owner, files={"file": ("period.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        identifier = analysis["analysis_id"]
        metrics = analysis["pdf_processing_metrics"]
        assert metrics["document_period_unavailable"] == 1
        assert "document_period_unavailable" in metrics["canonical_warning_types_list"]
        assert metrics["export_recommendation"] == "review_recommended"
        assert [row["amount"] for row in analysis["preview_transactions"]] == amounts
        report = client.get(f"/report/{identifier}", params=owner)
        assert report.status_code == 200
        stored = json.loads((tmp_path / "analyses" / identifier / "analysis.json").read_text(encoding="utf-8"))
        assert stored["pdf_processing_metrics"]["document_period_unavailable"] == 1
        for fmt in ("csv", "xlsx", "ofx"):
            exported = client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt})
            assert exported.status_code == 200, exported.text
            if fmt == "ofx":
                assert exported.text.count("<STMTTRN>") == len(amounts)
        assert client.get("/report/an_period_nonexistent", params=owner).status_code == 404


def test_supported_csv_remains_compatible_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-period-csv"}
        raw = b"date,description,amount\n2026-10-01,Entrada teste,100\n2026-10-02,Saida teste,-20\n"
        response = client.post("/convert", data=owner, files={"file": ("teste.csv", raw, "text/csv")})
        assert response.status_code == 200, response.text
        identifier = response.json()["analysis"]["analysis_id"]
        assert client.get(f"/report/{identifier}", params=owner).status_code == 200
        assert client.post("/analyze").status_code == 405
