"""Explicit paid OCR selection over the shared financial interpretation."""

from dataclasses import replace
from hashlib import sha256

from app.application.asynchronous_textract import AsynchronousTextractReader
from app.application.conversion.pro_conversion import ConversionProQuota, inspect_pro_pdf
from app.application.errors import InvalidFileContentError
from app.application.ocr_cash_statement import project_shared_cash_extraction as project_pro_extraction


def make_pro_pdf_parser(*, access, job, reader=None):
    budget = ConversionProQuota(access)

    def parse(raw, *, max_ocr_pages=None, on_ocr_progress=None):
        budget.require_access(job.identity)
        limit = min(budget.policy.max_pages, max_ocr_pages or budget.policy.max_pages)
        pages = inspect_pro_pdf(raw, max_pages=limit)
        access.ensure_quota_available(job.identity, required_units=pages if job.identity.quota_mode == "pages" else 1)
        budget.reserve(job.job_id, job.identity, pages)
        try:
            token = sha256(f"{job.identity.identity_id}:{job.job_id}:{sha256(raw).hexdigest()}:analysis-v1".encode()).hexdigest()
            extraction = (reader or AsynchronousTextractReader()).read(
                raw,
                max_pages=limit,
                request_token=token,
                before_document=lambda count: budget.before_document(job.job_id, job.identity, count),
                on_progress=on_ocr_progress,
                on_rejected_document=lambda count: budget.reject_document(job.job_id, job.identity, count),
            )
            try:
                projected = project_pro_extraction(extraction)
                return replace(
                    projected,
                    parse_metrics={**projected.parse_metrics, "conversion_mode": "pro", "textract_used": 1, "textract_attempted": 1},
                )
            except InvalidFileContentError as exc:
                setattr(exc, "_ocr_source_extraction", extraction)
                raise
        except InvalidFileContentError:
            raise
        except Exception as exc:
            raise InvalidFileContentError(
                "Não foi possível concluir a Conversão PRO. Confira o resultado antes de enviar uma nova solicitação."
            ) from exc
        finally:
            budget.finish(job.job_id, job.identity)

    return parse
