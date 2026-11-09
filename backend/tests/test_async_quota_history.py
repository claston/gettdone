import hashlib
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.application.access_control import AccessControlService
from app.application.admin_dashboard_service import AdminDashboardService
from app.application.conversion.conversion_batch import ConversionBatch
from app.application.conversion.conversion_batch_repository import InMemoryConversionBatchRepository
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.conversion.conversion_document_store import ConversionDocumentReference
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.conversion_job_repository import ConversionJobStatus
from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.pro_conversion import ConversionProQuota
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.dependencies import get_access_control_service, get_conversion_batch_service, get_conversion_runtime_config
from app.main import app
from app.workers.conversion_lambda import ConversionLambdaProcessor
from tests.http_runtime import LoopbackClient
from tests.test_auth_session_http_real_api import _run_http_server
from tests.test_conversion_batches_api import FakeDirectUploadService, FakePublisher, _request_payload


def _identity(access, kind, suffix="one"):
    if kind == "anonymous":
        auth = {"anonymous_fingerprint": f"quota-browser-{suffix}"}
        return access.resolve_identity(**auth, user_token=None), auth
    user = access.register_user(name="Quota Test", email=f"quota-{suffix}@example.com", password="strong-pass")
    if kind == "pages":
        access.activate_user_plan(user_id=user.user_id, plan_code="essencial")
    return access.resolve_identity(anonymous_fingerprint=None, user_token=user.token), {"user_token": user.token}


def _history(access, identity):
    with access._connect() as conn:
        if identity.identity_type == "anonymous":
            rows = access._fetchall(conn, "SELECT * FROM anonymous_conversion_events WHERE anonymous_fingerprint = ?",
                                    (identity.identity_id,))
        else:
            rows = access._fetchall(conn, "SELECT * FROM user_conversions WHERE user_id = ?", (identity.identity_id,))
        return [dict(row) for row in rows]


def _batch_service():
    return ConversionBatchService(
        repository=InMemoryConversionBatchRepository(),
        direct_upload_service=FakeDirectUploadService(),
        queue_publisher=FakePublisher(calls=[]),
    )


@pytest.fixture
def quota_api(tmp_path):
    access = AccessControlService(state_file=tmp_path / "access.json", token_secret="test-secret")
    service = _batch_service()
    app.dependency_overrides[get_access_control_service] = lambda: access
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"},
    )
    try:
        with TestClient(app) as client:
            yield client, access, service
    finally:
        app.dependency_overrides.clear()
        access.close()


def _reject(client, auth, files, key="quota-request"):
    return client.post("/api/conversion-batches", headers={"Idempotency-Key": key}, json={**auth, "files": files})


@pytest.mark.parametrize("kind", ["anonymous", "registered", "pages"])
def test_exhausted_quota_is_recorded_before_upload_and_retries_do_not_duplicate(quota_api, kind):
    client, access, service = quota_api
    identity, auth = _identity(access, kind)
    access.consume_quota(identity, consumed_units=identity.quota_limit)
    files = _request_payload(1)["files"]
    first = _reject(client, auth, files)
    assert first.status_code == 429
    code = "monthly_pages_quota_exceeded" if kind == "pages" else "weekly_quota_exceeded"
    assert first.json()["detail"]["code"] == code
    records = _history(access, identity)
    assert len(records) == 1
    record = records[0]
    assert record["status"] == "Falha"
    assert record["error_code"] == "quota_exceeded"
    assert record["error_stage"] == "batch_preflight"
    assert record["error_subcode"] == code
    assert record["filename"] == files[0]["filename"]
    assert record["file_sha256"] == files[0]["sha256_hex"]
    assert record["pages_count"] is None
    assert not record["ocr_used"] and record["ocr_pages_processed"] == 0

    assert _reject(client, auth, files).status_code == 429
    assert len(_history(access, identity)) == 1
    dashboard = AdminDashboardService(access).get_dashboard()
    assert dashboard["summary"]["non_conversion_count"] == 1
    assert dashboard["summary"]["conversions_total"] == 0
    assert _reject(client, auth, files, key="new-attempt").status_code == 429
    assert len(_history(access, identity)) == 2
    assert access.get_remaining_quota(identity) == 0
    assert service.direct_upload_service.counter == 0
    assert service.queue_publisher.calls == []


def test_rejected_batch_records_each_file_and_keeps_owners_separate(quota_api):
    client, access, service = quota_api
    files = _request_payload(2)["files"]
    for suffix in ("first", "second"):
        identity, auth = _identity(access, "pages", suffix)
        access.consume_quota(identity, consumed_units=identity.quota_limit)
        assert _reject(client, auth, files).status_code == 429
        assert _reject(client, auth, files).status_code == 429
        records = _history(access, identity)
        assert len(records) == 2
        assert {row["filename"] for row in records} == {item["filename"] for item in files}
    assert AdminDashboardService(access).get_dashboard()["summary"]["non_conversion_count"] == 4
    assert service.direct_upload_service.counter == 0


def test_history_failure_does_not_turn_quota_rejection_into_a_server_error(quota_api, monkeypatch, caplog):
    client, access, _ = quota_api
    identity, auth = _identity(access, "anonymous")
    access.consume_quota(identity, consumed_units=identity.quota_limit)

    def fail(**kwargs):
        raise RuntimeError("private-file-name-must-not-be-logged.pdf")

    monkeypatch.setattr(access, "record_anonymous_conversion_event", fail)
    assert _reject(client, auth, _request_payload(1)["files"]).status_code == 429
    assert "batch_quota_history_persist_failed" in caplog.text
    assert "private-file-name" not in caplog.text


def test_exhausted_pro_ocr_budget_is_recorded_without_consuming_regular_quota(quota_api, monkeypatch):
    monkeypatch.setenv("CONVERSION_PRO_ENABLED", "true")
    monkeypatch.setenv("CONVERSION_PRO_MONTHLY_PAGES", "1")
    client, access, service = quota_api
    identity, auth = _identity(access, "pages")
    budget = ConversionProQuota(access)
    budget.reserve("previous-pro-job", identity, 1)
    budget.before_document("previous-pro-job", identity, 1)
    budget.finish("previous-pro-job", identity)
    payload = {**auth, "files": _request_payload(1)["files"], "ocr_mode": "pro"}
    for _ in range(2):
        response = client.post("/api/conversion-batches", headers={"Idempotency-Key": "pro-budget"}, json=payload)
        assert response.status_code == 429
    records = _history(access, identity)
    assert len(records) == 1
    assert records[0]["error_code"] == "quota_exceeded"
    assert records[0]["error_subcode"] == "pro_monthly_pages_quota_exceeded"
    assert not records[0]["ocr_used"] and records[0]["ocr_pages_processed"] == 0
    assert access.get_remaining_quota(identity) == identity.quota_limit
    assert budget.remaining(identity) == 0
    assert service.direct_upload_service.counter == 0
    assert service.queue_publisher.calls == []


@pytest.mark.real_http
def test_quota_history_over_real_http_and_supported_conversion_still_works():
    with _run_http_server() as (base_url, access):
        service = _batch_service()
        app.dependency_overrides[get_conversion_batch_service] = lambda: service
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
            {"CONVERSION_ARCHITECTURE_MODE": "async_aws"},
        )
        identity, auth = _identity(access, "anonymous")
        access.consume_quota(identity, consumed_units=identity.quota_limit)
        with LoopbackClient(base_url=base_url, timeout=10) as client:
            for _ in range(2):
                response = _reject(client, auth, _request_payload(1)["files"])
                assert response.status_code == 429, response.text
                assert response.json()["detail"]["code"] == "weekly_quota_exceeded"
            assert len(_history(access, identity)) == 1
            assert service.direct_upload_service.counter == 0
            assert service.queue_publisher.calls == []
            # A different identity with available quota can still convert and download.
            assert client.post("/auth/anonymous-session", json={}).status_code == 200
            converted = client.post("/convert", files={
                "file": ("fictional.csv", b"date,description,amount\n2026-11-08,TESTE,40.00\n", "text/csv"),
            })
            assert converted.status_code == 200, converted.text
            assert client.get(f"/report/{converted.json()['processing_id']}").status_code == 200
            assert client.get("/report/nonexistent-quota-test").status_code == 404
            assert client.post("/analyze").status_code == 405


@pytest.mark.parametrize("kind", ["anonymous", "registered", "pages"])
def test_worker_records_quota_exhausted_after_queueing_once(tmp_path, kind):
    access = AccessControlService(state_file=tmp_path / "access.json", token_secret="test-secret")
    identity, _ = _identity(access, kind)
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    stream = BytesIO()
    writer.write(stream)
    raw = stream.getvalue()
    document = ingest_uploaded_document("statement.pdf", raw)
    batch = ConversionBatch.create(identity_type=identity.identity_type, identity_id=identity.identity_id,
                                   files_count=1, idempotency_key="worker-quota")
    job = ConversionJob.create(
        batch_id=batch.batch_id, identity=identity, scanned_likely=False, estimated_pages_count=1,
        document=ConversionDocumentReference(storage_key="doc_quota", filename="statement.pdf", file_type="pdf",
                                             size_bytes=len(raw), sha256_hex=hashlib.sha256(raw).hexdigest()),
    )
    repository = InMemoryConversionBatchRepository()
    repository.create(batch=batch, jobs=[job])
    repository.mark_uploaded(job.job_id)
    repository.mark_queued(job.job_id)
    # Another job exhausts the account while this job is already queued.
    access.consume_quota(identity, consumed_units=identity.quota_limit)

    class DocumentStore:
        def load(self, reference):
            return document

    processor = ConversionLambdaProcessor(
        repository=repository, document_store=DocumentStore(),
        pipeline=DocumentConversionPipeline(report_service=object(), access_control_service=access),
    )
    try:
        for attempt in (1, 2):
            processor.process(job_id=job.job_id, expected_batch_id=batch.batch_id, trace_id="quota-test",
                              receive_count=attempt, lease_owner=f"worker-{attempt}")
        assert repository.get(job.job_id).status == ConversionJobStatus.FAILED
        records = _history(access, identity)
        assert len(records) == 1
        assert records[0]["error_code"] == "quota_exceeded"
        assert records[0]["file_sha256"] == hashlib.sha256(raw).hexdigest()
        assert access.get_remaining_quota(identity) == 0
    finally:
        access.close()
