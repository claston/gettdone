from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.application.access_control import AccessControlService
from app.application.errors import InvalidFileContentError
from app.dependencies import get_access_control_service, get_analyze_service, get_report_service
from app.main import app
from app.schemas import (
    AnalyzeResponse,
    BeforeAfterPreview,
    CategorySummary,
    Insight,
    OperationalSummary,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)


class FakeAnalyzeService:
    def analyze(self, filename: str, raw_bytes: bytes, on_ocr_progress=None) -> AnalyzeResponse:
        _ = on_ocr_progress
        if not filename.endswith((".csv", ".xlsx", ".ofx", ".pdf")):
            from app.application import UnsupportedFileTypeError

            raise UnsupportedFileTypeError

        return AnalyzeResponse(
            analysis_id="an_convert123",
            file_type="pdf",
            transactions_total=1,
            total_inflows=100.0,
            total_outflows=-20.0,
            net_total=80.0,
            operational_summary=OperationalSummary(
                total_volume=120.0,
                inflow_count=1,
                outflow_count=1,
                reconciled_entries=0,
                unmatched_entries=1,
            ),
            reconciliation=ReconciliationSummary(
                matched_groups=0,
                reversed_entries=0,
                potential_duplicates=0,
            ),
            categories=[CategorySummary(category="Outros", total=-20.0, count=1)],
            top_expenses=[
                TopExpense(
                    description="TEST",
                    amount=-20.0,
                    date="2026-04-01",
                    category="Outros",
                )
            ],
            insights=[Insight(type="test", title="Test insight", description=f"Bytes: {len(raw_bytes)}")],
            preview_transactions=[
                TransactionPreview(
                    date="2026-04-01",
                    description="TEST",
                    amount=-20.0,
                    category="Outros",
                    reconciliation_status="unmatched",
                )
            ],
            preview_before_after=[
                BeforeAfterPreview(
                    date="2026-04-01",
                    description_before="test",
                    description_after="TEST",
                    amount_before=-20.0,
                    amount_after=-20.0,
                )
            ],
            expires_at=None,
        )


class InsufficientTextAnalyzeService:
    def analyze(self, filename: str, raw_bytes: bytes, on_ocr_progress=None) -> AnalyzeResponse:
        _ = (filename, raw_bytes, on_ocr_progress)
        raise InvalidFileContentError("Não encontramos texto suficiente para OCR neste PDF.")


class FakeReportService:
    def set_convert_owner(self, analysis_id: str, identity_type: str, identity_id: str) -> None:
        _ = (analysis_id, identity_type, identity_id)


class FailingAnonymousTelemetryAccessControlService(AccessControlService):
    def record_anonymous_conversion_event(self, **kwargs) -> None:
        _ = kwargs
        raise RuntimeError("telemetry storage unavailable")


def build_client(tmp_path) -> TestClient:
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    return TestClient(app)


def build_client_with_access_control(access_control: AccessControlService) -> TestClient:
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    return TestClient(app)


def build_client_with_overrides(access_control: AccessControlService, analyze_service) -> TestClient:
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_analyze_service] = lambda: analyze_service
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    return TestClient(app)


def _build_pdf_with_pages(page_count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(max(1, int(page_count))):
        writer.add_blank_page(width=612, height=792)
    payload = BytesIO()
    writer.write(payload)
    return payload.getvalue()


def test_convert_happy_path(tmp_path) -> None:
    client = build_client(tmp_path)
    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-a"},
        files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processing_id"] == "an_convert123"
    assert payload["identity_type"] == "anonymous"
    assert payload["quota_remaining"] == 2
    assert payload["quota_limit"] == 3
    assert payload["analysis"]["analysis_id"] == "an_convert123"
    app.dependency_overrides.clear()


def test_convert_rejects_unsupported_file_type(tmp_path) -> None:
    client = build_client(tmp_path)
    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-b"},
        files={"file": ("sample.txt", b"unsupported", "text/plain")},
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_convert_rejects_file_larger_than_2mb(tmp_path) -> None:
    client = build_client(tmp_path)
    oversized = b"a" * ((2 * 1024 * 1024) + 1)

    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-c"},
        files={"file": ("sample.pdf", oversized, "application/pdf")},
    )

    assert response.status_code == 413
    assert "maximum size of 2 MB" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_convert_rejects_ocr_pdf_larger_than_2mb_for_paid_user(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.convert._inspect_pdf_scan_likely",
        lambda filename, raw_bytes: (True, 1),
    )
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    user = access_control.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    access_control.activate_user_plan(user_id=user.user_id, plan_code="profissional")
    client = build_client_with_access_control(access_control)
    oversized = b"a" * ((2 * 1024 * 1024) + 1)

    response = client.post(
        "/convert",
        data={"user_token": user.token},
        files={"file": ("sample.pdf", oversized, "application/pdf")},
    )

    assert response.status_code == 413
    assert "maximum size of 2 MB" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_convert_allows_text_pdf_up_to_10mb(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.convert._inspect_pdf_scan_likely",
        lambda filename, raw_bytes: (False, 1),
    )
    client = build_client(tmp_path)
    text_pdf = b"a" * ((2 * 1024 * 1024) + 1)

    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-text-size"},
        files={"file": ("sample.pdf", text_pdf, "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["identity_type"] == "anonymous"
    app.dependency_overrides.clear()


def test_convert_rejects_text_pdf_larger_than_10mb(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.convert._inspect_pdf_scan_likely",
        lambda filename, raw_bytes: (False, 1),
    )
    client = build_client(tmp_path)
    oversized = b"a" * ((10 * 1024 * 1024) + 1)

    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-text-too-large"},
        files={"file": ("sample.pdf", oversized, "application/pdf")},
    )

    assert response.status_code == 413
    assert "maximum size of 10 MB" in response.json()["detail"]
    app.dependency_overrides.clear()


def test_convert_rejects_pdf_above_max_pages_per_file(tmp_path) -> None:
    client = build_client(tmp_path)
    oversized_pdf = _build_pdf_with_pages(16)

    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-many-pages"},
        files={"file": ("sample.pdf", oversized_pdf, "application/pdf")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "pages_limit_exceeded"
    assert detail["pages_count"] == 16
    assert detail["max_pages_per_file"] == 15
    app.dependency_overrides.clear()


def test_convert_rejects_ocr_pdf_above_15_pages_for_paid_user(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.convert._inspect_pdf_scan_likely",
        lambda filename, raw_bytes: (True, 16),
    )
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    user = access_control.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    access_control.activate_user_plan(user_id=user.user_id, plan_code="profissional")
    client = build_client_with_access_control(access_control)

    response = client.post(
        "/convert",
        data={"user_token": user.token},
        files={"file": ("sample.pdf", b"%PDF scanned", "application/pdf")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "pages_limit_exceeded"
    assert detail["pages_count"] == 16
    assert detail["max_pages_per_file"] == 15
    app.dependency_overrides.clear()


def test_convert_returns_pages_limit_when_ocr_like_pdf_is_misdetected_as_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.convert._inspect_pdf_scan_likely",
        lambda filename, raw_bytes: (False, 16),
    )
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    user = access_control.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    access_control.activate_user_plan(user_id=user.user_id, plan_code="profissional")
    client = build_client_with_overrides(access_control, InsufficientTextAnalyzeService())

    response = client.post(
        "/convert",
        data={"user_token": user.token},
        files={"file": ("sample.pdf", b"%PDF ocr-like", "application/pdf")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "pages_limit_exceeded"
    assert detail["pages_count"] == 16
    assert detail["max_pages_per_file"] == 15
    app.dependency_overrides.clear()


def test_convert_allows_text_pdf_up_to_250_pages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.convert._inspect_pdf_scan_likely",
        lambda filename, raw_bytes: (False, 250),
    )
    client = build_client(tmp_path)

    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-text-pages"},
        files={"file": ("sample.pdf", b"%PDF text", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["identity_type"] == "anonymous"
    app.dependency_overrides.clear()


def test_convert_rejects_text_pdf_above_250_pages(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.routers.convert._inspect_pdf_scan_likely",
        lambda filename, raw_bytes: (False, 251),
    )
    client = build_client(tmp_path)

    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-text-too-many-pages"},
        files={"file": ("sample.pdf", b"%PDF text", "application/pdf")},
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "pages_limit_exceeded"
    assert detail["pages_count"] == 251
    assert detail["max_pages_per_file"] == 250
    app.dependency_overrides.clear()


def test_convert_blocks_4th_attempt_with_structured_quota_detail(tmp_path) -> None:
    client = build_client(tmp_path)
    data = {"anonymous_fingerprint": "anon-fp-d"}
    files = {"file": ("sample.pdf", b"%PDF data", "application/pdf")}

    assert client.post("/convert", data=data, files=files).status_code == 200
    assert client.post("/convert", data=data, files=files).status_code == 200
    assert client.post("/convert", data=data, files=files).status_code == 200

    blocked = client.post("/convert", data=data, files=files)
    assert blocked.status_code == 429
    detail = blocked.json()["detail"]
    assert detail["code"] == "weekly_quota_exceeded"
    assert detail["identity_type"] == "anonymous"
    assert detail["quota_limit"] == 3
    assert detail["quota_remaining"] == 0
    assert detail["upgrade_url"] == "./signup.html?next=%2Fofx-convert.html&reason=quota"
    assert isinstance(detail["reset_at"], str)
    assert "T" in detail["reset_at"]
    app.dependency_overrides.clear()


def test_convert_succeeds_when_anonymous_telemetry_persistence_fails(tmp_path) -> None:
    access_control = FailingAnonymousTelemetryAccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    app.dependency_overrides[get_access_control_service] = lambda: access_control
    app.dependency_overrides[get_analyze_service] = lambda: FakeAnalyzeService()
    app.dependency_overrides[get_report_service] = lambda: FakeReportService()
    client = TestClient(app)

    response = client.post(
        "/convert",
        data={"anonymous_fingerprint": "anon-fp-telemetry-fail"},
        files={"file": ("sample.pdf", b"%PDF data", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["identity_type"] == "anonymous"
    app.dependency_overrides.clear()
