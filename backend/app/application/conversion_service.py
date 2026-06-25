from app.application.conversion import document_preflight_service as document_preflight_service_module
from app.application.conversion.document_conversion_pipeline import DocumentConversionPipeline
from app.application.conversion.uploaded_document import UploadedDocument
from app.schemas import ConvertResponse

TEXT_PDF_MAX_PAGES_PER_FILE = document_preflight_service_module.TEXT_PDF_MAX_PAGES_PER_FILE
TEXT_PDF_MAX_UPLOAD_SIZE_BYTES = document_preflight_service_module.TEXT_PDF_MAX_UPLOAD_SIZE_BYTES
OCR_PDF_MAX_PAGES_PER_FILE = document_preflight_service_module.OCR_PDF_MAX_PAGES_PER_FILE
OCR_PDF_MAX_UPLOAD_SIZE_BYTES = document_preflight_service_module.OCR_PDF_MAX_UPLOAD_SIZE_BYTES


class ConversionService:
    """Temporary compatibility facade around the application conversion pipeline."""

    def __init__(self, *, document_conversion_pipeline: DocumentConversionPipeline) -> None:
        self.document_conversion_pipeline = document_conversion_pipeline

    def build_convert_response(
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
    ) -> ConvertResponse:
        return self.document_conversion_pipeline.run(
            document=document,
            anonymous_fingerprint=anonymous_fingerprint,
            user_token=user_token,
            authorization=authorization,
            access_cookie_token=access_cookie_token,
            on_ocr_progress=on_ocr_progress,
            scanned_likely=scanned_likely,
            estimated_pages_count=estimated_pages_count,
        )
