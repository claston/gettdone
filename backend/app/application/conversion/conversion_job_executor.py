from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.conversion.conversion_document_store import ConversionDocumentStore
from app.application.conversion.conversion_job import ConversionExecutionHooks, ConversionJob
from app.application.conversion.conversion_job_repository import (
    ConversionJobRepository,
    ConversionJobResultReference,
)
from app.application.conversion.conversion_pipeline_result import ConversionPipelineResult, ConversionPipelineStatus


class ConversionJobExecutor(Protocol):
    def execute(
        self,
        *,
        job: ConversionJob,
        hooks: ConversionExecutionHooks,
    ) -> ConversionPipelineResult: ...


class ConversionJobRunner(Protocol):
    def run_job(
        self,
        *,
        job: ConversionJob,
        hooks: ConversionExecutionHooks,
    ) -> ConversionPipelineResult: ...


@dataclass(frozen=True, slots=True)
class InlineConversionJobExecutor:
    document_conversion_pipeline: ConversionJobRunner
    document_store: ConversionDocumentStore
    job_repository: ConversionJobRepository

    def execute(
        self,
        *,
        job: ConversionJob,
        hooks: ConversionExecutionHooks,
    ) -> ConversionPipelineResult:
        self.job_repository.mark_running(job.job_id)
        try:
            document = self.document_store.load(job.document)
            result = self.document_conversion_pipeline.run_job(
                job=job,
                document=document,
                hooks=hooks,
            )
            self._persist_result(job, result)
            return result
        except Exception as exc:
            self.job_repository.mark_failed(
                job.job_id,
                code=type(exc).__name__,
                message=str(exc),
            )
            raise
        finally:
            self.document_store.delete(job.document)

    def _persist_result(self, job: ConversionJob, result: ConversionPipelineResult) -> None:
        payload = result.payload or {}
        if result.status == ConversionPipelineStatus.COMPLETED:
            analysis_id = str(payload.get("processing_id") or payload.get("analysis_id") or "").strip()
            self.job_repository.mark_completed(
                job.job_id,
                result=ConversionJobResultReference(analysis_id=analysis_id),
            )
            return
        if result.status == ConversionPipelineStatus.REJECTED:
            self.job_repository.mark_failed(
                job.job_id,
                code=result.rejection_reason or "rejected",
                message=result.message,
            )
            return
        self.job_repository.mark_failed(
            job.job_id,
            code="conversion_failed",
            message=result.message,
        )
