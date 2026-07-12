import json
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import pytest

from app.application.access_control import IdentityContext
from app.application.conversion.conversion_document_store import FilesystemConversionDocumentStore
from app.application.conversion.conversion_job import ConversionExecutionHooks, ConversionJob
from app.application.conversion.conversion_job_executor import InlineConversionJobExecutor
from app.application.conversion.conversion_pipeline_result import ConversionPipelineResult
from app.application.conversion.uploaded_document import UploadedDocument, UploadedDocumentStage


def _document() -> UploadedDocument:
    staged_path = Path(__file__).parent / "fixtures" / "document_conversion_pipeline_statement.csv"
    return UploadedDocument.from_staged_upload(
        filename="statement.csv",
        staged_upload=UploadedDocumentStage(
            path=staged_path,
            size_bytes=staged_path.stat().st_size,
            sha256_hex="abc123",
        ),
    )


def test_conversion_job_is_immutable_and_keeps_execution_hooks_outside_payload(tmp_path: Path) -> None:
    store = FilesystemConversionDocumentStore(root_dir=tmp_path / "jobs")
    job = ConversionJob.create(
        document=store.store(_document()),
        identity=IdentityContext(identity_type="anonymous", identity_id="anon_123", quota_limit=3),
        scanned_likely=True,
        estimated_pages_count=3,
    )

    assert job.document.filename == "statement.csv"
    assert job.preflight_result.scanned_likely is True
    assert job.preflight_result.estimated_pages_count == 3
    assert not hasattr(job, "on_ocr_progress")
    assert not hasattr(job, "user_token")
    serialized = json.dumps(asdict(job), sort_keys=True)
    assert '"storage_key"' in serialized
    assert "statement.csv" in serialized
    with pytest.raises(FrozenInstanceError):
        job.job_id = "changed"  # type: ignore[misc]


def test_inline_conversion_job_executor_materializes_and_deletes_document(tmp_path: Path) -> None:
    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[tuple[ConversionJob, UploadedDocument, ConversionExecutionHooks]] = []

        def run_job(
            self,
            *,
            job: ConversionJob,
            document: UploadedDocument,
            hooks: ConversionExecutionHooks,
        ) -> ConversionPipelineResult:
            self.calls.append((job, document, hooks))
            return ConversionPipelineResult.rejected(reason="test", message="Rejected for test.")

    pipeline = FakePipeline()
    store = FilesystemConversionDocumentStore(root_dir=tmp_path / "jobs")
    executor = InlineConversionJobExecutor(
        document_conversion_pipeline=pipeline,
        document_store=store,
    )

    def callback(current: int, total: int) -> None:
        _ = current, total

    hooks = ConversionExecutionHooks(on_ocr_progress=callback)
    job = ConversionJob.create(
        document=store.store(_document()),
        identity=IdentityContext(identity_type="anonymous", identity_id="anon_123", quota_limit=3),
    )

    result = executor.execute(job=job, hooks=hooks)

    assert result.rejection_reason == "test"
    assert pipeline.calls[0][0] == job
    assert pipeline.calls[0][1].raw_bytes == _document().raw_bytes
    assert pipeline.calls[0][2] == hooks
    with pytest.raises(FileNotFoundError):
        store.load(job.document)
