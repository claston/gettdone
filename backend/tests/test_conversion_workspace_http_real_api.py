from __future__ import annotations

import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.application.access_control import IdentityContext
from app.application.conversion.async_conversion_rollout import AsyncConversionRolloutPolicy
from app.application.conversion.contracts.documents import ConversionDocumentReference
from app.application.conversion.contracts.jobs import ConversionJobResultReference
from app.application.conversion.conversion_batch import ConversionBatch
from app.application.conversion.conversion_batch_repository import InMemoryConversionBatchRepository
from app.application.conversion.conversion_batch_service import ConversionBatchService
from app.application.conversion.conversion_job import ConversionJob
from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
from app.application.conversion.uploaded_document import ingest_uploaded_document
from app.dependencies import (
    get_access_control_service,
    get_async_conversion_report_service,
    get_async_conversion_rollout_policy,
    get_conversion_batch_service,
    get_conversion_document_store,
    get_conversion_runtime_config,
)
from app.main import app

pytestmark = pytest.mark.real_http


class FakeAccessControl:
    def resolve_identity(self, *, anonymous_fingerprint, user_token):
        if anonymous_fingerprint != "workspace-owner" or user_token not in {None, ""}:
            return IdentityContext(identity_type="anonymous", identity_id="other", quota_limit=20)
        return IdentityContext(identity_type="anonymous", identity_id="owner", quota_limit=20)


class FakeDocumentStore:
    def load(self, reference):
        return ingest_uploaded_document(reference.filename, b"%PDF-1.4\nworkspace\n")


class FakeReportService:
    def __init__(self, report_path: Path) -> None:
        self.report_path = report_path

    def get_convert_report_path(self, analysis_id: str, file_format: str):
        assert analysis_id == "an_http_workspace"
        assert file_format == "ofx"
        return self.report_path


class NoopUploadService:
    pass


class NoopPublisher:
    def publish(self, **_kwargs):
        return "message"


def _service() -> tuple[ConversionBatchService, str, str]:
    repository = InMemoryConversionBatchRepository()
    identity = IdentityContext(identity_type="anonymous", identity_id="owner", quota_limit=20)
    batch = ConversionBatch.create(
        identity_type=identity.identity_type,
        identity_id=identity.identity_id,
        files_count=1,
        idempotency_key="http-workspace",
    )
    document = ConversionDocumentReference(
        storage_key="doc_0123456789abcdef01234567",
        filename="extrato-http.pdf",
        file_type="pdf",
        size_bytes=1024,
        sha256_hex="1" * 64,
    )
    job = ConversionJob.create(batch_id=batch.batch_id, document=document, identity=identity)
    repository.create(batch=batch, jobs=[job])
    repository.mark_uploaded(job.job_id)
    repository.mark_queued(job.job_id)
    repository.mark_running(job.job_id)
    repository.mark_completed(
        job.job_id,
        result=ConversionJobResultReference(
            analysis_id="an_http_workspace",
            payload={
                "processing_id": "an_http_workspace",
                "analysis": {"transactions_total": 1, "preview_transactions": []},
            },
        ),
    )
    return (
        ConversionBatchService(
            repository=repository,
            direct_upload_service=NoopUploadService(),
            queue_publisher=NoopPublisher(),
        ),
        batch.batch_id,
        job.job_id,
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _run_http_server(tmp_path: Path):
    service, batch_id, job_id = _service()
    report_path = tmp_path / "converted.ofx"
    report_path.write_text("OFXHEADER:100\n", encoding="utf-8")
    app.dependency_overrides[get_access_control_service] = lambda: FakeAccessControl()
    app.dependency_overrides[get_async_conversion_report_service] = lambda: FakeReportService(report_path)
    app.dependency_overrides[get_async_conversion_rollout_policy] = lambda: AsyncConversionRolloutPolicy.from_mapping(
        {}
    )
    app.dependency_overrides[get_conversion_batch_service] = lambda: service
    app.dependency_overrides[get_conversion_document_store] = lambda: FakeDocumentStore()
    app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
        {"CONVERSION_ARCHITECTURE_MODE": "async_aws"}
    )
    host = "127.0.0.1"
    port = _find_free_port()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://{host}:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        raise RuntimeError("HTTP test server did not start in time")
    try:
        yield base_url, batch_id, job_id
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        app.dependency_overrides.clear()


def test_http_workspace_batch_document_review_export_and_negative_owner(tmp_path: Path) -> None:
    with _run_http_server(tmp_path) as (base_url, batch_id, job_id):
        params = {"anonymous_fingerprint": "workspace-owner"}
        batch = httpx.get(f"{base_url}/api/conversion-batches/{batch_id}", params=params)
        document = httpx.get(
            f"{base_url}/api/conversion-batches/{batch_id}/jobs/{job_id}/document",
            params=params,
        )
        reviewed = httpx.post(
            f"{base_url}/api/conversion-batches/{batch_id}/jobs/{job_id}/review",
            json=params,
        )
        exported = httpx.get(f"{base_url}/api/conversion-batches/{batch_id}/export", params=params)
        forbidden = httpx.get(
            f"{base_url}/api/conversion-batches/{batch_id}",
            params={"anonymous_fingerprint": "not-owner"},
        )

    assert batch.status_code == 200
    assert document.status_code == 200 and document.content.startswith(b"%PDF")
    assert reviewed.status_code == 200 and reviewed.json()["reviewed_at"]
    assert exported.status_code == 200 and exported.content.startswith(b"PK")
    assert forbidden.status_code == 404
