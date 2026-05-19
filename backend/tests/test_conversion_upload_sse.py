import json
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.application import InvalidFileContentError
from app.dependencies import get_access_control_service, get_analyze_service, get_report_service
from app.main import app
from app.schemas import (
    AnalyzeResponse,
    BeforeAfterPreview,
    CategorySummary,
    ConvertResponse,
    Insight,
    OperationalSummary,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)


def _blank_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class FakeAnalyzeService:
    def analyze(self, filename: str, raw_bytes: bytes, on_ocr_progress=None) -> AnalyzeResponse:
        if "fail_ocr" in filename:
            raise InvalidFileContentError("OCR failed while processing PDF pages.")
        if on_ocr_progress is not None:
            on_ocr_progress(1, 2)
            on_ocr_progress(2, 2)
        return AnalyzeResponse(
            analysis_id="an_sse_001",
            file_type="pdf",
            transactions_total=1,
            total_inflows=10.0,
            total_outflows=-2.0,
            net_total=8.0,
            operational_summary=OperationalSummary(
                total_volume=12.0,
                inflow_count=1,
                outflow_count=1,
                reconciled_entries=0,
                unmatched_entries=1,
            ),
            reconciliation=ReconciliationSummary(matched_groups=0, reversed_entries=0, potential_duplicates=0),
            categories=[CategorySummary(category="Outros", total=8.0, count=1)],
            top_expenses=[TopExpense(description="TESTE", amount=-2.0, date="2026-05-17", category="Outros")],
            insights=[Insight(type="test", title="ok", description="ok")],
            preview_transactions=[
                TransactionPreview(
                    date="2026-05-17",
                    description="TESTE",
                    amount=-2.0,
                    category="Outros",
                    reconciliation_status="unmatched",
                )
            ],
            preview_before_after=[
                BeforeAfterPreview(
                    date="2026-05-17",
                    description_before="teste",
                    description_after="TESTE",
                    amount_before=-2.0,
                    amount_after=-2.0,
                )
            ],
            expires_at=None,
        )


class FakeReportService:
    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        _ = (analysis_id, identity_type, identity_id)


class FakeAccessControlService:
    class _Identity:
        def __init__(self) -> None:
            self.identity_type = "anonymous"
            self.identity_id = "anon_fp"
            self.quota_limit = 5
            self.max_upload_size_bytes = 2 * 1024 * 1024
            self.quota_mode = "conversion"

    def resolve_identity(self, anonymous_fingerprint: str | None, user_token: str | None):
        _ = (anonymous_fingerprint, user_token)
        return self._Identity()

    def assert_upload_size(self, raw_bytes: bytes, max_upload_size_bytes: int) -> None:
        _ = (raw_bytes, max_upload_size_bytes)

    def ensure_quota_available(self, identity, required_units: int = 1) -> None:
        _ = (identity, required_units)

    def consume_quota(self, identity, consumed_units: int = 1) -> int:
        _ = (identity, consumed_units)
        return 4

    def record_user_conversion(self, **kwargs) -> None:
        _ = kwargs


def _build_client() -> TestClient:
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    return TestClient(app)


def _parse_sse_payloads(body_text: str) -> list[dict]:
    events = []
    for block in body_text.split("\n\n"):
        lines = block.splitlines()
        for line in lines:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_streaming_upload_emits_minimum_events_and_completed() -> None:
    client = _build_client()
    response = client.post(
        "/api/conversions/upload",
        headers={"accept": "text/event-stream"},
        data={"anonymous_fingerprint": "fp-sse"},
        files={"file": ("sample.pdf", _blank_pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    payloads = _parse_sse_payloads(response.text)
    stages = [item.get("stage") for item in payloads]
    assert "upload_received" in stages
    assert "pdf_inspection" in stages
    assert "scan_detection" in stages
    assert "completed" in stages
    completed = next(item for item in payloads if item.get("stage") == "completed")
    assert completed["conversionId"] == "an_sse_001"


def test_streaming_upload_marks_scanned_pdf_and_emits_ocr_progress() -> None:
    client = _build_client()
    response = client.post(
        "/api/conversions/upload",
        headers={"accept": "text/event-stream"},
        data={"anonymous_fingerprint": "fp-scan"},
        files={"file": ("scan_sample.pdf", _blank_pdf_bytes(), "application/pdf")},
    )
    payloads = _parse_sse_payloads(response.text)
    scan_event = next(item for item in payloads if item.get("stage") == "scan_detection")
    assert scan_event["scannedLikely"] is True
    assert any(item.get("stage") == "ocr_started" for item in payloads)
    assert any(item.get("stage") == "ocr_progress" for item in payloads)


def test_streaming_upload_emits_failed_event_for_ocr_failure() -> None:
    client = _build_client()
    response = client.post(
        "/api/conversions/upload",
        headers={"accept": "text/event-stream"},
        data={"anonymous_fingerprint": "fp-fail"},
        files={"file": ("fail_ocr.pdf", _blank_pdf_bytes(), "application/pdf")},
    )
    payloads = _parse_sse_payloads(response.text)
    failed = next(item for item in payloads if item.get("stage") == "failed")
    assert failed["code"] in {"insufficient_text", "invalid_pdf_content"}
    assert isinstance(failed["retryable"], bool)


def test_upload_without_sse_accept_keeps_json_fallback() -> None:
    client = _build_client()
    response = client.post(
        "/api/conversions/upload",
        data={"anonymous_fingerprint": "fp-json"},
        files={"file": ("sample.pdf", _blank_pdf_bytes(), "application/pdf")},
    )
    assert response.status_code == 200
    payload = ConvertResponse.model_validate(response.json())
    assert payload.processing_id == "an_sse_001"

