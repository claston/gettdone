"""Exercise public/worker exports with synthetic Textract column evidence."""
import hashlib

import httpx
import pytest

from app.application import pdf_parser
from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import (
    get_access_control_service,
    get_analysis_repository,
    get_async_conversion_report_service,
    get_conversion_batch_service,
    get_conversion_processing_pipeline,
    get_report_service,
)
from app.main import app
from app.workers.conversion_lambda import ConversionLambdaProcessor
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server
from tests.test_convert_edits_http_real_api import _run_http_server
from tests.test_stone_ocr_values import change_line, stone_extraction

pytestmark = pytest.mark.real_http


def configure_textract(monkeypatch, kind, *, mode="analysis", ambiguous=False):
    extraction = stone_extraction(kind)
    if ambiguous:
        extraction = change_line(extraction, "R$ 5.735,76", "R$ 5.736,76")
    blocks = [{"Id": line.id, "BlockType": "LINE", "Page": 1, "Text": line.text,
               "Confidence": line.confidence, "Geometry": {"BoundingBox": {
                   key.title(): value for key, value in line.bbox.items()
               }}} for line in extraction.pages[0].lines]
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "true")
    monkeypatch.setenv("TEXTRACT_TEMP_BUCKET", "synthetic-unused-bucket")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    monkeypatch.setattr(pdf_parser.TextractGateway, "analyze_pdf", lambda *a, **kw: {
        "document_hash": "synthetic-stone", "page_count": 1, "blocks": blocks, "metrics": {"textract_mode": mode},
    })
    return generate_native_text_pdf((("Synthetic OCR source",),))


def assert_analysis(analysis, kind):
    assert analysis["transactions_total"] == 5
    row = analysis["preview_transactions"][1]
    assert row["amount"] == {"missing_fee": -.15, "malformed_balance": -.34, "type_after_name": -102}[kind]
    if kind == "missing_fee":
        assert "ocr_amount_recovered_from_balances" in row["warning_types"]
        assert row["running_balance"] == 5589.91
    elif kind == "malformed_balance":
        assert "ocr_balance_recovered_from_context" in row["warning_types"]
        assert row["running_balance"] == 10128.34


@pytest.mark.parametrize("kind", ["missing_fee", "malformed_balance", "type_after_name", "csv"])
def test_public_http_stone_ocr_and_export(kind, tmp_path, monkeypatch):
    raw = configure_textract(monkeypatch, kind)
    filename, mime = "stone.pdf", "application/pdf"
    if kind == "csv":
        filename, mime = "statement.csv", "text/csv"
        raw = b"date,description,amount\n2026-04-23,PIX,100\n"
    storage = TempAnalysisStorage(root_dir=tmp_path / "public")

    def configure(_access):
        app.dependency_overrides[get_analysis_repository] = lambda: storage
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)

    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        owner = {"anonymous_fingerprint": "fp-stone-synthetic"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        if kind != "csv":
            assert_analysis(analysis, kind)
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        exported = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": "ofx"})
        assert exported.status_code == 200, exported.text
        assert exported.text.count("<STMTTRN>") == (1 if kind == "csv" else 5)
        amount = {"missing_fee": -.15, "malformed_balance": -.34, "type_after_name": -102, "csv": 100}[kind]
        assert f"<TRNAMT>{amount:.2f}" in exported.text
        assert client.get("/report/an_stone_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


def test_ambiguous_ocr_http_rejects_instead_of_exporting_balance_as_amount(tmp_path, monkeypatch):
    raw = configure_textract(monkeypatch, "missing_fee", ambiguous=True)

    def configure(_access):
        app.dependency_overrides[get_conversion_processing_pipeline] = lambda: build_default_conversion_pipeline()

    with _run_http_server(configure) as base_url, httpx.Client(base_url=base_url, timeout=20) as client:
        response = client.post("/convert", data={"anonymous_fingerprint": "fp-stone-ambiguous"},
                               files={"file": ("stone.pdf", raw, "application/pdf")})
        assert response.status_code == 400, response.text
        assert "Stone" in response.text
        assert "analysis" not in response.json()


@pytest.mark.parametrize("kind,mode", [("missing_fee", "text"), ("malformed_balance", "analysis"), ("type_after_name", "analysis")])
def test_worker_http_stone_ocr_preserves_warnings_and_values(kind, mode, tmp_path, monkeypatch):
    raw = configure_textract(monkeypatch, kind, mode=mode)

    class OriginalStore:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, raw)

    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        service = app.dependency_overrides[get_conversion_batch_service]()
        access = app.dependency_overrides[get_access_control_service]()
        storage = TempAnalysisStorage(root_dir=tmp_path / "analyses", ttl_seconds=3600)
        reports = ReportService(storage)
        processor = ConversionLambdaProcessor(repository=service.repository, document_store=OriginalStore(),
            pipeline=DocumentConversionPipeline(report_service=reports, access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(), analysis_repository=storage))
        app.dependency_overrides[get_async_conversion_report_service] = lambda: reports
        with httpx.Client(base_url=base_url, timeout=20, headers={"Authorization": f"Bearer {token}",
                "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}) as client:
            created = client.post("/api/conversion-batches", headers={"Idempotency-Key": kind}, json={"files": [
                {"filename": "stone.pdf", "content_type": "application/pdf", "size_bytes": len(raw),
                 "sha256_hex": hashlib.sha256(raw).hexdigest()},
            ]})
            assert created.status_code == 201, created.text
            batch_id = created.json()["batch_id"]
            assert client.post(f"/api/conversion-batches/{batch_id}/submit", json={}).status_code == 202
            processor.process(job_id=publisher.calls[0], expected_batch_id=batch_id, trace_id=kind,
                              receive_count=1, lease_owner="stone-synthetic-worker")
            job = client.get(f"/api/conversion-batches/{batch_id}").json()["jobs"][0]
            assert job["status"] == "completed", job
            assert_analysis(job["result"]["analysis"], kind)
            analysis_id = job["analysis_id"]
            for fmt in ("csv", "xlsx", "ofx"):
                exported = client.get(f"/convert-report/{analysis_id}", params={"format": fmt})
                assert exported.status_code == 200, exported.text
                if fmt == "ofx":
                    amount = {"missing_fee": -.15, "malformed_balance": -.34, "type_after_name": -102}[kind]
                    assert f"<TRNAMT>{amount:.2f}" in exported.text
                    assert exported.text.count("<STMTTRN>") == 5
