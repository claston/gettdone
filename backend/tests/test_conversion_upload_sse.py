import json
from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.application import FileTooLargeError, InvalidFileContentError, QuotaExceededError
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
    def analyze(self, filename: str, raw_bytes: bytes, on_ocr_progress=None, max_ocr_pages=None) -> AnalyzeResponse:
        if "fail_ocr" in filename:
            raise InvalidFileContentError("OCR failed while processing PDF pages.")
        if "corrupted" in filename:
            raise InvalidFileContentError("Ignoring wrong pointing object 9 0 (offset 0)")
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
            self.max_upload_size_bytes = 5 * 1024 * 1024
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

    def record_anonymous_conversion_event(self, **kwargs) -> None:
        _ = kwargs


class TooLargeAccessControlService(FakeAccessControlService):
    def assert_upload_size(self, raw_bytes: bytes, max_upload_size_bytes: int) -> None:
        _ = raw_bytes
        err = FileTooLargeError()
        setattr(err, "_max_upload_size_bytes", max_upload_size_bytes)
        raise err


class AnonymousQuotaExceededAccessControlService(FakeAccessControlService):
    def ensure_quota_available(self, identity, required_units: int = 1) -> None:
        _ = (identity, required_units)
        raise QuotaExceededError()


def _build_client() -> TestClient:
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControlService()
    return TestClient(app)


def _build_client_with_access_control(access_control_service) -> TestClient:
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    app.dependency_overrides[get_access_control_service] = lambda: access_control_service
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
    assert "document_received" in stages
    assert "document_analysis" in stages
    assert "document_preparation" in stages
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
    scan_event = next(item for item in payloads if item.get("stage") == "document_preparation")
    assert scan_event["scannedLikely"] is True
    assert any(item.get("stage") == "document_processing" for item in payloads)
    progress_values = [int(item.get("progress", 0)) for item in payloads if isinstance(item.get("progress"), int | float)]
    assert progress_values
    assert progress_values == sorted(progress_values)
    assert max(progress_values) == 100


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


def test_streaming_upload_emits_friendly_message_for_corrupted_pdf() -> None:
    client = _build_client()
    response = client.post(
        "/api/conversions/upload",
        headers={"accept": "text/event-stream"},
        data={"anonymous_fingerprint": "fp-corrupted"},
        files={"file": ("corrupted.pdf", _blank_pdf_bytes(), "application/pdf")},
    )
    payloads = _parse_sse_payloads(response.text)
    failed = next(item for item in payloads if item.get("stage") == "failed")
    assert failed["code"] == "invalid_pdf_content"
    assert failed["message"] == "Parece que seu arquivo PDF está corrompido."


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


def test_streaming_upload_non_scanned_progress_advances_to_conversion_stage() -> None:
    client = _build_client()
    response = client.post(
        "/api/conversions/upload",
        headers={"accept": "text/event-stream"},
        data={"anonymous_fingerprint": "fp-normal"},
        files={"file": ("statement.pdf", b"data,valor,descricao\n2026-05-17,10.0,PIX", "application/pdf")},
    )
    payloads = _parse_sse_payloads(response.text)
    conversion_event = next(item for item in payloads if item.get("stage") == "preview_generation")
    assert int(conversion_event.get("progress", 0)) >= 80


def test_streaming_upload_emits_file_too_large_error_message() -> None:
    client = _build_client_with_access_control(TooLargeAccessControlService())
    response = client.post(
        "/api/conversions/upload",
        headers={"accept": "text/event-stream"},
        data={"anonymous_fingerprint": "fp-too-large"},
        files={"file": ("sample.pdf", _blank_pdf_bytes(), "application/pdf")},
    )
    payloads = _parse_sse_payloads(response.text)
    failed = next(item for item in payloads if item.get("stage") == "failed")
    assert failed["code"] == "file_too_large"
    assert "5 mb" in str(failed["message"]).lower()


def test_streaming_upload_explains_scanned_pdf_pages_limit_without_numeric_limit(monkeypatch) -> None:
    monkeypatch.setenv("PDF_OCR_MAX_PAGES", "8")
    monkeypatch.setattr(
        "app.routers.convert._inspect_pdf_scan_likely",
        lambda filename, raw_bytes: (True, 11),
    )
    client = _build_client()
    response = client.post(
        "/api/conversions/upload",
        headers={"accept": "text/event-stream"},
        data={"anonymous_fingerprint": "fp-scanned-limit"},
        files={"file": ("scanned.pdf", b"%PDF scanned", "application/pdf")},
    )
    payloads = _parse_sse_payloads(response.text)
    failed = next(item for item in payloads if item.get("stage") == "failed")
    assert failed["code"] == "pages_limit_exceeded"
    assert failed["ocr_context"] == "scanned_pdf"
    assert failed["pages_count"] == 11
    assert failed["max_pages_per_file"] == 8
    assert "documento escaneado" in failed["message"]
    assert "11" not in failed["message"]
    assert "8" not in failed["message"]


def test_streaming_upload_emits_weekly_quota_failed_event_with_identity_type() -> None:
    client = _build_client_with_access_control(AnonymousQuotaExceededAccessControlService())
    response = client.post(
        "/api/conversions/upload",
        headers={"accept": "text/event-stream"},
        data={"anonymous_fingerprint": "fp-weekly-limit"},
        files={"file": ("sample.pdf", _blank_pdf_bytes(), "application/pdf")},
    )
    payloads = _parse_sse_payloads(response.text)
    failed = next(item for item in payloads if item.get("stage") == "failed")
    assert failed["code"] == "weekly_quota_exceeded"
    assert failed["identity_type"] == "anonymous"



