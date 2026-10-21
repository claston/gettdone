import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.late_recovery_statements import bb_zero_pages, caixa_manager, tracked_unicred_pdf, viacredi_pages
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case

pytestmark = pytest.mark.real_http


def native(pages):
    return generate_native_text_pdf(tuple(tuple(p.splitlines()) for p in pages))


@pytest.mark.parametrize("kind,count,bank,closing", [
    ("viacredi", 4, "085", 120), ("bb", 5, "001", 85), ("unicred", 5, "136", -80), ("caixa", 2, "104", 120),
])
def test_late_variants_http_downloads_preserve_verified_balances(kind, count, bank, closing, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = {"viacredi": lambda: native(viacredi_pages()), "bb": lambda: native(bb_zero_pages()),
           "unicred": tracked_unicred_pdf, "caixa": lambda: configure_ocr(monkeypatch, caixa_manager(rotated=True), "tables")}[kind]()
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-late-"+kind}
        response = client.post("/convert", data=owner, files={"file": (kind+".pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        data = response.json()["analysis"]
        assert data["transactions_total"] == count
        assert data["bank_code"] == bank
        assert data["closing_balance"] == closing
        assert data["pdf_processing_metrics"]["balance_consistency_failed"] == 0
        analysis_id = data["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": fmt})
            assert report.status_code == 200, report.text
            if fmt == "ofx":
                assert report.text.count("<STMTTRN>") == count
                assert f"<BALAMT>{closing:.2f}" in report.text
                assert ("<CCSTMTRS>" if kind == "unicred" else f"<BANKID>{bank}") in report.text


def test_http_csv_compatibility_negative_control_and_legacy_route(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-late-negative"}
        raw = native([p.replace("50,00-", "51,00-") for p in viacredi_pages()])
        response = client.post("/convert", data=owner, files={"file": ("bad.pdf", raw, "application/pdf")})
        assert response.status_code == 400
        assert "conferir" in response.json()["detail"]
        csv = b"date,description,amount\n2026-09-01,Example,100\n2026-09-02,Expense,-10\n"
        response = client.post("/convert", data=owner, files={"file": ("sample.csv", csv, "text/csv")})
        assert response.status_code == 200, response.text
        aid = response.json()["analysis"]["analysis_id"]
        assert client.get(f"/report/{aid}", params=owner).status_code == 200
        assert client.get("/report/an_late_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


@pytest.mark.parametrize("kind", ["unicred", "caixa"])
def test_worker_preserves_native_and_rotated_ocr_financial_controls(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = tracked_unicred_pdf() if kind == "unicred" else configure_ocr(monkeypatch, caixa_manager(rotated=True))
    with worker_case(tmp_path, raw, filename=kind+".pdf") as (client, job):
        data = job["result"]["analysis"]
        assert data["transactions_total"] == (5 if kind == "unicred" else 2)
        assert data["closing_balance"] == (-80 if kind == "unicred" else 120)
        assert client.get(f"/convert-report/{data['analysis_id']}", params={"format": "xlsx"}).status_code == 200
