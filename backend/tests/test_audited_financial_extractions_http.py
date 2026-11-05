"""Real HTTP checks for typed reports and corrected cash ledgers."""

import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import public_client
from tests.test_audited_financial_extractions import COLLECTION, PORTFOLIO, SAFRA, bradesco_continuation

pytestmark = pytest.mark.real_http


def test_audited_reports_keep_semantics_through_storage_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")

    def pdf(text):
        return generate_native_text_pdf((tuple(text.splitlines()),))

    cases = [
        ("cash.csv", b"date,description,amount\n2026-10-01,Entrada,100\n", 1, True),
        ("safra.pdf", pdf(SAFRA), 3, True),
        ("bradesco.pdf", bradesco_continuation(), 3, True),
        ("portfolio.pdf", pdf(PORTFOLIO), 0, False),
        ("collections.pdf", pdf(COLLECTION), 2, False),
    ]
    with public_client(tmp_path) as client:
        for name, raw, count, ofx in cases:
            owner = {"anonymous_fingerprint": "fp-seven-" + name}
            response = client.post(
                "/convert", data=owner, files={"file": (name, raw, "application/pdf" if name.endswith(".pdf") else "text/csv")}
            )
            assert response.status_code == 200, response.text
            data = response.json()["analysis"]
            assert data["transactions_total"] == count
            ident = data["analysis_id"]
            assert client.get("/report/" + ident, params=owner).status_code == 200
            for fmt in ("csv", "xlsx", "ofx"):
                exported = client.get("/convert-report/" + ident, params={**owner, "format": fmt})
                assert exported.status_code == (400 if fmt == "ofx" and not ofx else 200), exported.text
                if fmt == "ofx" and ofx:
                    assert exported.text.count("<STMTTRN>") == count
                if name == "portfolio.pdf" and fmt == "csv":
                    assert "amounts.provision_tax" in exported.text and "138.00" in exported.text
                if name == "collections.pdf" and fmt == "csv":
                    assert "2026-10-14" in exported.text and "2026-10-13" in exported.text
        assert client.get("/report/an_missing_seven", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405
