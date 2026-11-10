"""Real HTTP validation of recovered cash rows, review flags and typed exports."""
import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.ocr_conversion_http_helpers import public_client
from tests.test_recovery_sweep_ocr import pagbank
from tests.test_remaining_recovery_cases import bb_optional_reference_cells
from tests.test_wise_statements import wise_lines

pytestmark = pytest.mark.real_http


def test_recovered_documents_keep_rows_review_and_subaccounts_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    wise = [line.replace("30,00 1.380,00", "30,00 1.381,00") for line in wise_lines()]
    source = pagbank()
    cases = [
        ("supported.csv", b"date,description,amount\n2026-10-01,Entrada,100\n", 1),
        ("bb.pdf", grid_pdf([bb_optional_reference_cells()+[("Documento", .63, .1)]]), 1),
        ("wise.pdf", generate_native_text_pdf((tuple(wise),)), 6),
        ("pagbank.pdf", grid_pdf([[(line.text,line.bbox["left"],line.bbox["top"])
                                  for line in source.pages[0].lines]]), None),
    ]
    with public_client(tmp_path) as client:
        for name, raw, count in cases:
            owner = {"anonymous_fingerprint": "synthetic-nov09-"+name}
            response = client.post("/convert", data=owner, files={"file": (name, raw,
                "text/csv" if name.endswith(".csv") else "application/pdf")})
            assert response.status_code == 200, response.text
            analysis = response.json()["analysis"]
            if count is not None:
                assert analysis["transactions_total"] == count
            if name == "wise.pdf":
                metrics = analysis["pdf_processing_metrics"]
                assert metrics["financial_controls_validated"] == 0
                assert metrics["export_recommendation"] == "review_recommended"
            ident = analysis["analysis_id"]
            assert client.get("/report/"+ident, params=owner).status_code == 200
            for fmt in ("csv", "xlsx", "ofx"):
                exported = client.get("/convert-report/"+ident, params={**owner,"format":fmt})
                assert exported.status_code == (400 if name == "pagbank.pdf" and fmt == "ofx" else 200), exported.text
                if name == "pagbank.pdf" and fmt == "csv":
                    assert "receivables" in exported.text and "available" in exported.text
                elif fmt == "ofx" and count is not None:
                    assert exported.text.count("<STMTTRN>") == count
        assert client.get("/report/an_nov09_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405
