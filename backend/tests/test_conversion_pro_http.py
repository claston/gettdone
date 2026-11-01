"""Real HTTP, queued worker and downloads for the explicitly paid OCR path."""

import hashlib
from dataclasses import replace

import httpx
import pytest

from app.application import ocr_cash_statement
from app.application.conversion import pro_pdf_parser
from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.document_extraction_models import ExtractedLine
from app.application.pdf_layout_inference import PdfLayoutInference
from app.application.received_statements import ReceivedStatements
from app.application.report_service import ReportService
from app.application.storage_service import TempAnalysisStorage
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_report_service,
    get_conversion_batch_document_store,
    get_conversion_batch_service,
    get_report_service,
)
from app.main import app
from app.workers.conversion_lambda import ConversionLambdaProcessor
from tests.ocr_conversion_http_helpers import public_client
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server
from tests.test_embedded_ocr_quality import layered_pdf
from tests.test_ocr_structure_experiment import evidence
from tests.test_received_statements import record as received_record

pytestmark = pytest.mark.real_http


@pytest.mark.parametrize("low_confidence", [False, True])
def test_paid_individual_estimate_worker_report_and_idempotency(tmp_path, monkeypatch, low_confidence):
    raw = layered_pdf("")
    extraction = replace(evidence(tables=True), document_hash=hashlib.sha256(raw).hexdigest())
    page = extraction.pages[0]
    if low_confidence:
        table = page.tables[0]
        rows = [list(row) for row in table.rows]
        rows[1][2] = replace(rows[1][2], confidence=40)
        page = replace(page, tables=[replace(table, rows=rows)])
    headers = [
        ExtractedLine("owner", 1, 1, "EMPRESA AURORA LTDA"),
        ExtractedLine("issuer", 1, 2, "ASAAS"),
        ExtractedLine("origin", 1, 3, "CNPJ 12.345.678/0001-90 Agência: 0001 Conta: 0012345-6"),
    ]
    extraction = replace(extraction, pages=[replace(page, lines=[*headers, *page.lines])])
    calls = []

    def read(self, content, *, max_pages, request_token, before_document, on_progress, on_rejected_document):
        assert content == raw
        before_document(1)
        calls.append(1)
        on_progress(1, 1)
        return extraction

    monkeypatch.setenv("CONVERSION_PRO_ENABLED", "true")
    monkeypatch.setattr(pro_pdf_parser.AsynchronousTextractReader, "read", read)
    monkeypatch.setattr(
        ocr_cash_statement, "infer_pdf_layout", lambda text: PdfLayoutInference("asaas_extrato_conta_digital_movimentacoes_v1", 0.99, False)
    )

    class Store:
        def load(self, reference):
            return ingest_uploaded_document(reference.filename, raw)

    with _run_pro_upload_http_server(tmp_path) as (base, publisher, token):
        service = app.dependency_overrides[get_conversion_batch_service]()
        access = app.dependency_overrides[get_access_control_service]()
        storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")
        reports = ReportService(storage)
        processor = ConversionLambdaProcessor(
            repository=service.repository,
            document_store=Store(),
            pipeline=DocumentConversionPipeline(
                report_service=reports,
                access_control_service=access,
                processing_pipeline=build_default_conversion_pipeline(),
                analysis_repository=storage,
            ),
            receipt_recorder=ReceivedStatements(access.db).receive,
        )
        app.dependency_overrides[get_conversion_batch_document_store] = lambda: Store()
        app.dependency_overrides[get_async_conversion_report_service] = lambda: reports
        app.dependency_overrides[get_report_service] = lambda: reports
        with httpx.Client(
            base_url=base,
            headers={"Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"},
            timeout=20,
        ) as client:
            created = client.post(
                "/api/pro/conversion-batches",
                headers={"Idempotency-Key": "pro-http"},
                json={
                    "ocr_mode": "pro",
                    "files": [
                        {
                            "filename": "scan.pdf",
                            "content_type": "application/pdf",
                            "size_bytes": len(raw),
                            "sha256_hex": hashlib.sha256(raw).hexdigest(),
                        }
                    ],
                },
            )
            assert created.status_code == 201, created.text
            batch = created.json()["batch_id"]
            estimate = client.get(f"/api/pro/conversion-batches/{batch}/pro-estimate")
            assert estimate.status_code == 200, estimate.text
            assert estimate.json()["pages"] == 1 and not calls
            assert client.post(f"/api/pro/conversion-batches/{batch}/submit", json={}).status_code == 202
            job_id = publisher.calls[0]
            received = ReceivedStatements(access.db)
            legacy = received_record(service.repository.get(job_id).job.identity.identity_id, "legacy_asaas", days=30, source={
                "holder_name": "ASAAS", "holder_tax_id": "12345678000190", "bank_code": "461", "confidence": "strong"})
            received.receive(legacy)
            legacy_client_id = received.dashboard(legacy.job.identity.identity_id)["clients"][0]["id"]
            for receive_count in [1, 2]:
                processor.process(
                    job_id=job_id, expected_batch_id=batch, trace_id="pro-http", receive_count=receive_count, lease_owner="test"
                )
            result = client.get(f"/api/pro/conversion-batches/{batch}").json()["jobs"][0]
            assert result["status"] == "completed", result
            analysis = result["result"]["analysis"]
            assert analysis["transactions_total"] == 3
            assert analysis["pdf_processing_metrics"]["ocr_balance_arithmetic_matches"] == 1
            assert analysis["pdf_processing_metrics"]["ocr_review_rows_count"] == int(low_confidence)
            if low_confidence:
                assert analysis["preview_transactions"][0]["warning_types"] == ["ocr_amount_confidence_unconfirmed"]
                assert not analysis["preview_transactions"][1]["warning_types"]
            assert analysis["bank_name"] == "Asaas" and analysis["bank_code"] == "461"
            source = service.repository.get(job_id).result.payload["organization_source"]
            assert source["holder_name"] == "EMPRESA AURORA LTDA" and source["holder_tax_id"] == "12345678000190"
            receipts = client.get("/api/pro/receipts")
            assert receipts.status_code == 200, receipts.text
            assert receipts.json()["clients"][0]["name"] == "EMPRESA AURORA LTDA"
            assert len(receipts.json()["clients"]) == 1
            assert receipts.json()["clients"][0]["id"] == legacy_client_id
            assert [r["amount"] for r in analysis["preview_transactions"]] == [20, -5, -5]
            if not low_confidence:
                assert analysis["opening_balance"] == 100 and analysis["closing_balance"] == 110
            else:
                # Arithmetic over read controls does not promote uncertain evidence to verified export bounds.
                assert analysis["opening_balance"] is None and analysis["closing_balance"] is None
            assert client.get(f"/report/{result['analysis_id']}").status_code == 200
            download = client.get(f"/convert-report/{result['analysis_id']}", params={"format": "csv"})
            assert download.status_code == 200 and "TARIFA" in download.text
            assert client.get("/api/pro/conversion-options").json()["remaining_pages"] == 19
            assert len(calls) == 1


def test_standard_csv_http_and_negative_report_stay_compatible(tmp_path):
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "pro-compatibility"}
        response = client.post(
            "/convert", data=owner, files={"file": ("input.csv", b"date,description,amount\n2026-10-01,PIX,10\n", "text/csv")}
        )
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]["analysis_id"]
        assert client.get(f"/report/{analysis}", params=owner).status_code == 200
        assert client.get("/report/an_pro_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405
