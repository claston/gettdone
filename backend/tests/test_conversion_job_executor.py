from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

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


def test_conversion_job_is_immutable_and_keeps_execution_hooks_outside_payload() -> None:
    job = ConversionJob.from_inputs(
        document=_document(),
        anonymous_fingerprint="anon-fp",
        user_token=None,
        authorization=None,
        access_cookie_token=None,
        scanned_likely=True,
        estimated_pages_count=3,
    )

    assert job.document.filename == "statement.csv"
    assert job.preflight_result.scanned_likely is True
    assert job.preflight_result.estimated_pages_count == 3
    assert not hasattr(job, "on_ocr_progress")
    with pytest.raises(FrozenInstanceError):
        job.user_token = "changed"  # type: ignore[misc]


def test_inline_conversion_job_executor_forwards_job_and_hooks_to_pipeline() -> None:
    class FakePipeline:
        def __init__(self) -> None:
            self.calls: list[tuple[ConversionJob, ConversionExecutionHooks]] = []

        def run_job(
            self,
            *,
            job: ConversionJob,
            hooks: ConversionExecutionHooks,
        ) -> ConversionPipelineResult:
            self.calls.append((job, hooks))
            return ConversionPipelineResult.rejected(reason="test", message="Rejected for test.")

    pipeline = FakePipeline()
    executor = InlineConversionJobExecutor(document_conversion_pipeline=pipeline)

    def callback(current: int, total: int) -> None:
        _ = current, total

    hooks = ConversionExecutionHooks(on_ocr_progress=callback)
    job = ConversionJob.from_inputs(
        document=_document(),
        anonymous_fingerprint="anon-fp",
        user_token=None,
        authorization=None,
        access_cookie_token=None,
    )

    result = executor.execute(job=job, hooks=hooks)

    assert result.rejection_reason == "test"
    assert pipeline.calls == [(job, hooks)]
