import threading
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.application import AccessControlService, ReportService, TempAnalysisStorage
from app.application.conversion.conversion_capacity import ConversionCapacityController
from app.dependencies import get_access_control_service, get_conversion_capacity_controller, get_report_service
from app.main import app
from app.routers import reconcile as module


def files(bank=None):
    return {"bank_file": ("bank.csv", bank or b"date,description,amount\n2026-04-01,TEST,-100", "text/csv"),
            "sheet_file": ("sheet.csv", b"data,valor,descricao\n2026-04-01,100,TEST", "text/csv")}


@pytest.fixture
def runtime(tmp_path):
    service = AccessControlService(state_file=tmp_path / "state.json", token_secret="test-secret", anonymous_quota_limit=1)
    controller = ConversionCapacityController(max_concurrency=1, retry_after_seconds=1)
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")
    app.dependency_overrides[get_access_control_service] = lambda: service
    app.dependency_overrides[get_conversion_capacity_controller] = lambda: controller
    app.dependency_overrides[get_report_service] = lambda: ReportService(storage)
    try:
        yield service, controller, storage
    finally:
        app.dependency_overrides.clear()
        controller.close()
        service.close()


def test_reconcile_checks_identity_before_parsing(runtime, monkeypatch):
    def forbidden(**kw):
        pytest.fail("Invalid credentials must not reach a document parser")
    monkeypatch.setattr(module, "classify_document", forbidden)
    with TestClient(app) as client:
        response = client.post("/reconcile", files=files(), headers={"Authorization": "Bearer invalid"})
    assert response.status_code in {400, 401}


def test_reconcile_applies_capacity_and_releases_it(runtime):
    _, controller, _ = runtime
    lease = controller.try_acquire(source="test")
    try:
        with TestClient(app) as client:
            assert client.post("/reconcile", files=files()).status_code == 503
    finally:
        lease.release(outcome="test")
    assert controller.active_count == 0


def test_reconcile_bounds_file_size_before_parsing(runtime, monkeypatch):
    monkeypatch.setattr(module, "RECONCILE_MAX_FILE_BYTES", 1024, raising=False)
    with TestClient(app) as client:
        response = client.post("/reconcile", files=files(b"date,description,amount\n" + b"x" * 2048))
    assert response.status_code == 413


def test_reconcile_persists_anonymous_owner_and_enforces_quota(runtime, monkeypatch):
    original = module.classify_document
    threads = []
    monkeypatch.setattr(module, "classify_document", lambda **kw: threads.append(threading.current_thread().name) or original(**kw))
    with TestClient(app) as client, TestClient(app) as other:
        created = client.post("/reconcile", files=files())
        assert created.status_code == 200
        assert client.post("/reconcile", files=files()).status_code == 429
        other.post("/auth/anonymous-session", json={})
        assert other.get("/reconcile-report/" + created.json()["analysis_id"]).status_code == 403
        assert client.get("/reconcile-report/" + created.json()["analysis_id"]).status_code == 200
    assert threads and all(name.startswith("conversion-worker") for name in threads)


def test_reconcile_rejects_excessive_pdf_pages_before_classification(runtime, monkeypatch):
    writer = PdfWriter()
    for _ in range(251):
        writer.add_blank_page(width=100, height=100)
    buffer = BytesIO()
    writer.write(buffer)
    def forbidden(**kw):
        raise AssertionError("Oversized PDF must not reach classification")
    monkeypatch.setattr(module, "classify_document", forbidden)
    payload = files()
    payload["bank_file"] = ("bank.pdf", buffer.getvalue(), "application/pdf")
    with TestClient(app) as client:
        assert client.post("/reconcile", files=payload).status_code == 413


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_upload_body_limit_counts_stream_without_trusting_content_length(runtime, monkeypatch, declared_length):
    from app.upload_limits import UPLOAD_BODY_LIMITS
    monkeypatch.setitem(UPLOAD_BODY_LIMITS, "/reconcile", 128)
    boundary = "test-boundary"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="bank_file"; filename="bank.csv"\r\n\r\n'.encode(),
        b"x" * 256,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if declared_length:
        headers["Content-Length"] = declared_length
    with TestClient(app) as client:
        response = client.post("/reconcile", content=iter(parts), headers=headers)
    assert response.status_code == 413
