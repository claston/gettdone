"""Exercise corrected PDF rows and inactive statements through real local HTTP."""
from pathlib import Path

import httpx
import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_admin_dashboard_http_real_api import _run_http_server
from tests.test_oct26_recovery import caixa_empty_lines, caixa_wrapped_lines

pytestmark = pytest.mark.real_http


def test_oct26_corrected_pdfs_convert_and_download_with_real_http(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    files = [
        ("synthetic.csv", b"date,description,amount\n2026-09-01,SYNTHETIC,40.00\n", "text/csv", 1),
        ("wrapped.pdf", generate_native_text_pdf((tuple(caixa_wrapped_lines()),)), "application/pdf", 4),
        ("inactive.pdf", generate_native_text_pdf((tuple(caixa_empty_lines()),)), "application/pdf", 0),
    ]
    with _run_http_server(tmp_path) as base_url, httpx.Client(base_url=base_url, timeout=15) as client:
        login = client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"})
        assert login.status_code == 200
        for name, raw, mime, count in files:
            response = client.post("/convert", files={"file": (name, raw, mime)})
            assert response.status_code == 200, response.text
            identifier = response.json()["processing_id"]
            analysis = response.json()["analysis"]
            assert analysis["transactions_total"] == count
            assert client.get(f"/report/{identifier}").status_code == 200
            export = client.get(f"/convert-report/{identifier}", params={"format": "ofx"})
            assert export.status_code == 200
            assert export.text.count("<STMTTRN>") == count
            if name == "wrapped.pdf":
                assert "<TRNAMT>-30.00" in export.text
                assert "<TRNAMT>-2.00" in export.text
            if name == "inactive.pdf":
                assert analysis["opening_balance"] == analysis["closing_balance"] == -150
                assert "<BANKID>104" in export.text
                assert "<ACCTID>0000000123456" in export.text
        assert client.get("/report/an_nonexistent_oct26").status_code == 404
        assert client.post("/analyze").status_code == 405
