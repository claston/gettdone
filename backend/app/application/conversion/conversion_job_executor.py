from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.conversion.conversion_job import ConversionExecutionHooks, ConversionJob
from app.application.conversion.conversion_pipeline_result import ConversionPipelineResult


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

    def execute(
        self,
        *,
        job: ConversionJob,
        hooks: ConversionExecutionHooks,
    ) -> ConversionPipelineResult:
        return self.document_conversion_pipeline.run_job(job=job, hooks=hooks)
