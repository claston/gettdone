from app.application.conversion.document_conversion_pipeline import (
    DocumentConversionPipeline,
    StagedUploadRef,
)
from app.schemas import ConvertResponse


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
        filename: str,
        staged_upload: StagedUploadRef,
        anonymous_fingerprint: str | None,
        user_token: str | None,
        authorization: str | None,
        access_cookie_token: str | None,
        on_ocr_progress=None,
        scanned_likely: bool | None = None,
        estimated_pages_count: int | None = None,
    ) -> ConvertResponse:
        return self.document_conversion_pipeline.run(
            filename=filename,
            staged_upload=staged_upload,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            on_ocr_progress=on_ocr_progress,
            scanned_likely=scanned_likely,
            estimated_pages_count=estimated_pages_count,
        )
