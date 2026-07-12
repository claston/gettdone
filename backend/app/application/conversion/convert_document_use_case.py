from app.application.conversion.conversion_job import ConversionExecutionHooks
from app.application.conversion.conversion_job_executor import ConversionJobExecutor
from app.application.conversion.conversion_job_factory import ConversionJobFactory
from app.application.conversion.conversion_pipeline_result import (
    ConversionPipelineResult,
    ConversionPipelineStatus,
)
from app.application.conversion.convert_document_result import ConvertDocumentResult
from app.application.conversion.uploaded_document import UploadedDocument


class ConvertDocumentUseCase:
    """Application entrypoint for the document conversion flow.

    It builds an immutable job and delegates execution behind an executor
    boundary while keeping the existing endpoint contract stable.
    """

    def __init__(
        self,
        *,
        conversion_job_factory: ConversionJobFactory,
        conversion_job_executor: ConversionJobExecutor,
    ) -> None:
        self.conversion_job_factory = conversion_job_factory
        self.conversion_job_executor = conversion_job_executor

    def execute(
        self,
        *,
        document: UploadedDocument,
        anonymous_fingerprint: str | None,
        user_token: str | None,
        authorization: str | None,
        access_cookie_token: str | None,
        on_ocr_progress=None,
        scanned_likely: bool | None = None,
        estimated_pages_count: int | None = None,
    ) -> ConvertDocumentResult:
        job = self.conversion_job_factory.create(
            document=document,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            scanned_likely=scanned_likely,
            estimated_pages_count=estimated_pages_count,
        )
        pipeline_result = self.conversion_job_executor.execute(
            job=job,
            hooks=ConversionExecutionHooks(on_ocr_progress=on_ocr_progress),
        )
        return self._to_convert_document_result(pipeline_result)

    def _to_convert_document_result(
        self,
        pipeline_result: ConversionPipelineResult,
    ) -> ConvertDocumentResult:
        payload = pipeline_result.payload or {}
        analysis_id = str(payload.get("processing_id") or payload.get("analysis_id") or "").strip()
        if pipeline_result.status == ConversionPipelineStatus.COMPLETED:
            if not analysis_id or pipeline_result.payload is None:
                raise RuntimeError("Completed ConversionPipelineResult must include payload with processing_id.")
            return ConvertDocumentResult.completed(
                analysis_id=analysis_id,
                payload=pipeline_result.payload,
            )
        if pipeline_result.status == ConversionPipelineStatus.REJECTED:
            return ConvertDocumentResult.rejected(
                analysis_id=analysis_id,
                reason=pipeline_result.rejection_reason or "rejected",
                message=pipeline_result.message,
            )
        return ConvertDocumentResult.failed(
            analysis_id=analysis_id,
            message=pipeline_result.message,
        )
