"""Exercise additive provenance through real HTTP, conversion and review storage."""
import httpx
import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import get_analysis_repository, get_conversion_processing_pipeline, get_report_service
from app.main import app
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_convert_edits_http_real_api import _run_http_server
from tests.test_wise_statements import wise_lines

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("file_type", ["csv", "pdf", "sicoob"])
def test_http_conversion_report_edit_and_negative_paths_keep_source_regions(tmp_path, monkeypatch, file_type):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    raw = (b"date,description,amount\n2026-04-01,RECEBIMENTO,40\n" if file_type == "csv"
           else generate_native_text_pdf((tuple(wise_lines()),)))
    if file_type == "sicoob":
        from tests.fixtures.sicoob_source_pdf import sicoob_source_pdf

        raw = sicoob_source_pdf()
        file_type = "pdf"
    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        response = client.post("/convert", data={"anonymous_fingerprint": "fp-regions"},
                               files={"file": (f"synthetic.{file_type}", raw, "text/csv" if file_type == "csv" else "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        analysis_id = analysis["analysis_id"]
        row = analysis["preview_transactions"][0]
        assert bool(row["source_regions"]) == (file_type == "pdf")
        params = {"anonymous_fingerprint": "fp-regions"}
        report = client.get(f"/report/{analysis_id}", params=params)
        assert report.status_code == 200 and report.content.startswith(b"PK")
        edited = client.post(f"/convert-edits/{analysis_id}", params=params, json={
            "expected_updated_at": analysis["updated_at"], "edits": [{"row_id": row["transaction_id"],
                "date": "2026-04-02", "description": "EDITED SOURCE TEST", "credit": 55,
                "source_regions": [{"page": 99, "left": 0, "top": 0, "width": 1, "height": 1}]}],
        })
        assert edited.status_code == 200, edited.text
        saved = next(item for item in edited.json()["preview_transactions"] if item["transaction_id"] == row["transaction_id"])
        assert saved["source_regions"] == row["source_regions"]
        if file_type == "pdf":
            report_before = client.get(f"/report/{analysis_id}", params=params).content
            marker = {"page": 1, "left": 0.1, "top": 0.6, "width": 0.7, "height": 0.03}
            patch = {"row_id": row["transaction_id"], "action": "annotate", "review_regions": [marker]}
            invalid = client.post(f"/convert-edits/{analysis_id}", params=params,
                json={"edits": [{**patch, "review_regions": [{**marker, "width": 2}]}]})
            assert invalid.status_code == 400, invalid.text
            assert client.post(f"/convert-edits/{analysis_id}", params={"anonymous_fingerprint": "other-owner"},
                json={"edits": [patch]}).status_code == 403
            marked = client.post(f"/convert-edits/{analysis_id}", params=params,
                json={"expected_updated_at": edited.json()["updated_at"], "operation_id": "mark-http", "edits": [patch]})
            assert marked.status_code == 200, marked.text
            marked_row = next(item for item in marked.json()["preview_transactions"] if item["transaction_id"] == row["transaction_id"])
            assert marked_row["review_regions"] == [marker]
            assert marked_row["source_regions"] == row["source_regions"]
            assert marked_row["amount"] == saved["amount"]
            assert client.get(f"/report/{analysis_id}", params=params).content == report_before
            cleared = client.post(f"/convert-edits/{analysis_id}", params=params,
                json={"expected_updated_at": marked.json()["updated_at"], "edits": [{**patch, "review_regions": []}]})
            assert cleared.status_code == 200 and cleared.json()["preview_transactions"][0]["review_regions"] == []
        assert client.get("/report/an_region_nonexistent", params=params).status_code == 404
        assert client.get(f"/report/{analysis_id}", params={"anonymous_fingerprint": "other-owner"}).status_code == 403
        # This historical route is intentionally disabled in the current app.
        assert client.post("/analyze", files={"file": ("data.csv", raw)}).status_code == 405
