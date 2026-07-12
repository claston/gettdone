from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.conversion.conversion_document_store import ConversionDocumentStore
from app.application.conversion.conversion_job import ConversionExecutionHooks, ConversionJob
from app.application.conversion.conversion_pipeline_result import ConversionPipelineResult
from app.application.conversion.uploaded_document import UploadedDocument


class ConversionJobExecutor(Protocol):
    def execute(
        self,
        *,
        job: ConversionJob,
        document: UploadedDocument,
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

    def execute(
        self,
        *,
        job: ConversionJob,
        hooks: ConversionExecutionHooks,
    ) -> ConversionPipelineResult:
        try:
            document = self.document_store.load(job.document)
            return self.document_conversion_pipeline.run_job(
                job=job,
                document=document,
                hooks=hooks,
            )
        finally:
            self.document_store.delete(job.document)
