"""Actual HTTP uploads, report downloads and financial rejection paths."""

import httpx
import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import get_analysis_repository, get_conversion_processing_pipeline, get_report_service
from app.main import app
from tests.fixtures.split_sheet_statements import BUILDERS, change, document
from tests.test_convert_edits_http_real_api import _run_http_server

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("kind", ["bb", "billing", "amazon", "csv"])
def test_upload_and_all_exports_preserve_financial_rows(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    filename, mime = "synthetic.pdf", "application/pdf"
    if kind == "csv":
        filename, mime = "synthetic.csv", "text/csv"
        raw = b"date,description,amount\n2026-02-02,CREDITO,100\n2026-02-03,DEBITO,-20\n"
    else:
        raw = document(BUILDERS[kind](), shuffled=True)
    with _run_http_server(configure) as base, httpx.Client(base_url=base, timeout=20) as client:
        owner = {"anonymous_fingerprint": "fp-split-sheet"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == (2 if kind == "csv" else 3)
        if kind != "csv":
            assert analysis["closing_balance"] == {"bb": 0, "billing": 79.01, "amazon": 85}[kind]
            assert not any(row["warning_types"] for row in analysis["preview_transactions"])
            assert all(row["source_regions"] for row in analysis["preview_transactions"])
            if kind in {"bb", "billing"}:
                assert len({region["page"] for region in analysis["preview_transactions"][0]["source_regions"]}) >= 2
        analysis_id = analysis["analysis_id"]
        report = client.get(f"/report/{analysis_id}", params=owner)
        assert report.status_code == 200 and report.content.startswith(b"PK")
        for fmt in ("csv", "xlsx", "ofx"):
            params = {**owner, "format": fmt}
            if kind == "billing":
                # No bank is printed: OFX identity must be supplied by the user.
                params.update(bank_code="001", agency="1234", account="12345")
            exported = client.get(f"/convert-report/{analysis_id}", params=params)
            assert exported.status_code == 200, exported.text
            if fmt == "ofx":
                assert exported.text.count("<STMTTRN>") == (2 if kind == "csv" else 3)
                if kind == "amazon":
                    assert "<BANKID>003" in exported.text
                    assert "<TRNAMT>-20.00" in exported.text
        assert client.get("/report/an_split_sheet_missing", params=owner).status_code == 404
        assert client.post("/analyze", files={"file": ("data.csv", b"date,amount\n2026-01-01,1")}).status_code == 405


@pytest.mark.parametrize("kind,before,after", [("bb", "35,00", "35,01"), ("billing", "99.01", "99.02"), ("amazon", "R$ 85,00", "R$ 85,01")])
def test_inconsistent_printed_controls_rejected_over_http(kind, before, after, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = document(change(BUILDERS[kind](), before, after), shuffled=True)

    def configure(_access):
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()

    with _run_http_server(configure) as base, httpx.Client(base_url=base, timeout=20) as client:
        response = client.post(
            "/convert", data={"anonymous_fingerprint": "fp-split-sheet-invalid"}, files={"file": ("synthetic.pdf", raw, "application/pdf")}
        )
        assert response.status_code == 400, response.text
        assert "conferir" in response.text
