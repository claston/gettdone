from app.application.conversion.conversion_pipeline_result import (
    ConversionPipelineResult,
    ConversionPipelineStatus,
)
from app.application.conversion.convert_document_result import ConvertDocumentResult
from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.uploaded_document import UploadedDocument


class ConvertDocumentUseCase:
    """Application entrypoint for the document conversion flow.

    This incremental version delegates to the application conversion pipeline
    while keeping the existing endpoint contract stable.
    """

    def __init__(self, *, document_conversion_pipeline: DocumentConversionPipeline) -> None:
        self.document_conversion_pipeline = document_conversion_pipeline

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
        pipeline_result = self.document_conversion_pipeline.run(
            document=document,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            on_ocr_progress=on_ocr_progress,
            scanned_likely=scanned_likely,
            estimated_pages_count=estimated_pages_count,
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
