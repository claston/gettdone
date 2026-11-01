"""Explicit whole-document TABLES/LAYOUT analysis, isolated from standard OCR."""

import hashlib
import logging
import os
import time
from dataclasses import replace
from io import BytesIO

from botocore.config import Config
from pypdf import PdfReader

from app.application.errors import InvalidFileContentError
from app.application.textract_extraction_mapper import map_textract_blocks_to_extraction
from app.application.textract_gateway import _load_boto3

logger = logging.getLogger(__name__)
_REJECTED_START = {
    "AccessDeniedException",
    "InvalidParameterException",
    "InvalidS3ObjectException",
    "UnsupportedDocumentException",
    "DocumentTooLargeException",
    "BadDocumentException",
}


def _reading_failure(message, *, stage, cause=None, attempted=False):
    error = InvalidFileContentError(message)
    metrics = {
        "error_stage": "ocr",
        "error_subcode": "pro_ocr_" + stage,
        "textract_attempted": int(attempted),
        "textract_used": 0,
        "extraction_provider": "aws_textract",
        "conversion_mode": "pro",
    }
    if cause is not None:
        metrics["textract_error_type"] = type(cause).__name__
        response = getattr(cause, "response", {})
        details = response.get("Error", {}) if isinstance(response, dict) else {}
        if isinstance(details, dict):
            metrics["ocr_provider_error_code"] = str(details.get("Code", ""))[:100]
        metrics["ocr_provider_operation"] = str(getattr(cause, "operation_name", ""))[:100]
    error._parse_observability = metrics
    return error


class AsynchronousTextractReader:
    def __init__(self, *, bucket=None, s3_client=None, textract_client=None, poll_interval_seconds=None, timeout_seconds=None):
        self.bucket = (bucket or os.getenv("TEXTRACT_TEMP_BUCKET", "")).strip()
        self.s3, self.textract = s3_client, textract_client
        self.poll_interval = max(
            0, float(poll_interval_seconds if poll_interval_seconds is not None else os.getenv("TEXTRACT_JOB_POLL_INTERVAL_SECONDS", "2"))
        )
        self.timeout = max(
            0.01, float(timeout_seconds if timeout_seconds is not None else os.getenv("TEXTRACT_JOB_TIMEOUT_SECONDS", "600"))
        )

    def read(self, raw, *, max_pages, request_token, before_document=None, on_progress=None, on_rejected_document=None):
        document = PdfReader(BytesIO(raw))
        page_count = len(document.pages)
        if document.is_encrypted or not 1 <= page_count <= max_pages:
            raise ValueError("PDF fora dos limites da Conversão PRO.")
        if not self.bucket:
            raise _reading_failure(
                "O serviço da Conversão PRO não está configurado neste ambiente. Avise o suporte antes de reenviar o arquivo.",
                stage="configuration",
            )
        if self.s3 is None or self.textract is None:
            try:
                session = _load_boto3().session.Session(
                    region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
                )
                self.s3 = self.s3 or session.client("s3")
                self.textract = self.textract or session.client(
                    "textract", config=Config(retries={"total_max_attempts": 1}, read_timeout=60, connect_timeout=10)
                )
            except Exception as exc:
                raise _reading_failure(
                    "O serviço da Conversão PRO não está configurado corretamente. Avise o suporte antes de reenviar.",
                    stage="configuration",
                    cause=exc,
                ) from exc
        prefix = os.getenv("TEXTRACT_S3_PREFIX", "").strip().strip("/") or "textract/tmp"
        key = f"{prefix}/analysis/{request_token}.pdf"
        try:
            uploaded = self.s3.put_object(Bucket=self.bucket, Key=key, Body=raw, ContentType="application/pdf")
        except Exception as exc:
            raise _reading_failure(
                "Não foi possível preparar o arquivo para a Conversão PRO. Avise o suporte antes de reenviar.", stage="upload", cause=exc
            ) from exc
        safe_to_delete = True
        job_id = ""
        try:
            if before_document:
                before_document(page_count)
            location = {"Bucket": self.bucket, "Name": key}
            if uploaded.get("VersionId"):
                location["Version"] = uploaded["VersionId"]
            # Once the request leaves us, a lost response can still mean an accepted job.
            safe_to_delete = False
            try:
                response = self.textract.start_document_analysis(
                    DocumentLocation={"S3Object": location}, FeatureTypes=["TABLES", "LAYOUT"], ClientRequestToken=request_token
                )
            except Exception as exc:
                code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                if code in _REJECTED_START:
                    safe_to_delete = True
                    if on_rejected_document:
                        on_rejected_document(page_count)
                raise _reading_failure(
                    "O serviço de leitura não conseguiu iniciar a Conversão PRO. Avise o suporte antes de reenviar.",
                    stage="analysis",
                    cause=exc,
                    attempted=True,
                ) from exc
            job_id = str(response.get("JobId") or "").strip()
            if not job_id:
                raise _reading_failure(
                    "O serviço não confirmou o início da Conversão PRO. Avise o suporte antes de reenviar.",
                    stage="analysis",
                    attempted=True,
                )
            deadline, blocks, next_token, seen_tokens = time.monotonic() + self.timeout, [], None, set()
            while time.monotonic() < deadline:
                params = {"JobId": job_id, "MaxResults": 1000}
                if next_token:
                    params["NextToken"] = next_token
                try:
                    result = self.textract.get_document_analysis(**params)
                except Exception as exc:
                    raise _reading_failure(
                        "Não foi possível consultar a leitura em andamento. Avise o suporte antes de reenviar.",
                        stage="analysis",
                        cause=exc,
                        attempted=True,
                    ) from exc
                status = str(result.get("JobStatus") or "").upper()
                if status == "IN_PROGRESS" and not next_token:
                    time.sleep(min(self.poll_interval, max(0, deadline - time.monotonic())))
                    continue
                if status in {"SUCCEEDED", "FAILED", "PARTIAL_SUCCESS"}:
                    safe_to_delete = True
                if (
                    status != "SUCCEEDED"
                    or result.get("Warnings")
                    or (
                        (result.get("DocumentMetadata") or {}).get("Pages") is not None
                        and int(result["DocumentMetadata"]["Pages"]) != page_count
                    )
                ):
                    raise _reading_failure(
                        "A leitura do PDF não foi concluída integralmente. Avise o suporte antes de reenviar.",
                        stage="analysis",
                        attempted=True,
                    )
                blocks.extend(result.get("Blocks") or [])
                next_token = result.get("NextToken")
                if not next_token:
                    break
                if next_token in seen_tokens:
                    raise _reading_failure(
                        "O serviço retornou uma leitura incompleta. Avise o suporte antes de reenviar.", stage="analysis", attempted=True
                    )
                seen_tokens.add(next_token)
            else:
                raise _reading_failure(
                    "A leitura excedeu o tempo de espera. Avise o suporte antes de enviar uma nova solicitação.",
                    stage="analysis",
                    attempted=True,
                )
            observed = {int(block.get("Page") or 1) for block in blocks}
            if observed != set(range(1, page_count + 1)):
                raise _reading_failure(
                    "Não foi possível confirmar a leitura de todas as páginas. Avise o suporte antes de reenviar.",
                    stage="analysis",
                    attempted=True,
                )
            extraction = map_textract_blocks_to_extraction(
                document_hash=hashlib.sha256(raw).hexdigest(), blocks=blocks, page_count=page_count, operation_id=job_id
            )
            if on_progress:
                for number in range(1, page_count + 1):
                    on_progress(number, page_count)
            return replace(
                extraction,
                metrics={**extraction.metrics, "textract_mode": "analysis", "textract_api_style": "async", "conversion_mode": "pro"},
            )
        finally:
            if safe_to_delete:
                delete = {"Bucket": self.bucket, "Key": key}
                if uploaded.get("VersionId"):
                    delete["VersionId"] = uploaded["VersionId"]
                try:
                    self.s3.delete_object(**delete)
                except Exception:
                    logger.warning("conversion_pro_temporary_cleanup_failed")
            else:
                # The bucket's temporary-prefix lifecycle must cover uncertain/running jobs.
                logger.warning("conversion_pro_temporary_input_retained job_id=%s key=%s", job_id, key)
