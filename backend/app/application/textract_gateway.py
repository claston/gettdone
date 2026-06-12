from __future__ import annotations

import hashlib
import os
import time
from io import BytesIO
from uuid import uuid4

from app.application.errors import InvalidFileContentError

DEFAULT_FEATURE_TYPES = ("TABLES", "LAYOUT")
TERMINAL_JOB_STATUSES = {"SUCCEEDED", "FAILED", "PARTIAL_SUCCESS"}
DEFAULT_TEXTRACT_MODE = "text"
TEXTRACT_ANALYSIS_MODE = "analysis"
TEXTRACT_TEXT_MODE = "text"


class TextractGateway:
    def __init__(
        self,
        *,
        bucket: str | None = None,
        region: str | None = None,
        prefix: str | None = None,
        poll_interval_seconds: float | None = None,
        timeout_seconds: float | None = None,
        feature_types: tuple[str, ...] | None = None,
        mode: str | None = None,
    ) -> None:
        self.bucket = (bucket or os.getenv("TEXTRACT_TEMP_BUCKET", "")).strip()
        if not self.bucket:
            raise InvalidFileContentError("OCR service is not configured for this environment.")
        self.region = (region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1").strip()
        self.prefix = (prefix or os.getenv("TEXTRACT_S3_PREFIX") or "textract/tmp/").strip()
        self.poll_interval_seconds = _to_float_env(
            poll_interval_seconds, "TEXTRACT_JOB_POLL_INTERVAL_SECONDS", default=2.0, min_value=0.2
        )
        self.timeout_seconds = _to_float_env(
            timeout_seconds, "TEXTRACT_JOB_TIMEOUT_SECONDS", default=600.0, min_value=5.0
        )
        self.mode = _resolve_textract_mode(mode)
        self.feature_types = feature_types or _resolve_feature_types()

    def analyze_pdf(self, *, raw_bytes: bytes) -> dict[str, object]:
        boto3 = _load_boto3()
        file_hash = hashlib.sha256(raw_bytes).hexdigest()
        s3_key = _build_s3_key(prefix=self.prefix, file_hash=file_hash)
        session = boto3.session.Session(region_name=self.region)
        s3_client = session.client("s3")
        textract_client = session.client("textract")

        deleted_s3_object = False
        timings_ms: dict[str, float] = {}
        upload_started = time.perf_counter()
        try:
            s3_client.upload_fileobj(BytesIO(raw_bytes), self.bucket, s3_key)
            timings_ms["textract_upload_ms"] = _elapsed_ms(upload_started)

            job_started = time.perf_counter()
            start_response = _start_textract_job(
                textract_client=textract_client,
                mode=self.mode,
                bucket=self.bucket,
                s3_key=s3_key,
                feature_types=self.feature_types,
            )
            job_id = str(start_response.get("JobId") or "").strip()
            if not job_id:
                raise InvalidFileContentError("OCR provider did not return a valid job id.")

            _wait_for_job(
                textract_client=textract_client,
                job_id=job_id,
                poll_interval_seconds=self.poll_interval_seconds,
                timeout_seconds=self.timeout_seconds,
                mode=self.mode,
            )
            timings_ms["textract_job_ms"] = _elapsed_ms(job_started)

            fetch_started = time.perf_counter()
            page_count, blocks, metadata = _fetch_textract_result(
                textract_client=textract_client,
                job_id=job_id,
                mode=self.mode,
            )
            timings_ms["textract_result_fetch_ms"] = _elapsed_ms(fetch_started)
            return {
                "provider": "aws_textract",
                "job_id": job_id,
                "document_hash": file_hash,
                "page_count": page_count,
                "blocks": blocks,
                "document_metadata": metadata,
                "metrics": {
                    "textract_used": 1,
                    "textract_page_count": page_count,
                    "textract_block_count": len(blocks),
                    "textract_mode": self.mode,
                    **timings_ms,
                },
            }
        except TimeoutError as exc:
            raise InvalidFileContentError(str(exc)) from exc
        except InvalidFileContentError:
            raise
        except Exception as exc:  # pragma: no cover - defensive against SDK edge cases
            raise InvalidFileContentError("OCR processing failed while reading the scanned PDF.") from exc
        finally:
            try:
                s3_client.delete_object(Bucket=self.bucket, Key=s3_key)
                deleted_s3_object = True
            except Exception:
                deleted_s3_object = False
            timings_ms["textract_s3_deleted"] = 1 if deleted_s3_object else 0


def _load_boto3():
    try:
        import boto3
    except Exception as exc:  # pragma: no cover - import guard
        raise InvalidFileContentError("OCR dependencies are not installed.") from exc
    return boto3


def _build_s3_key(*, prefix: str, file_hash: str) -> str:
    clean_prefix = str(prefix or "").strip().strip("/")
    file_name = f"{file_hash[:16]}-{uuid4().hex[:10]}.pdf"
    if clean_prefix:
        return f"{clean_prefix}/{file_name}"
    return file_name


def _wait_for_job(*, textract_client, job_id: str, poll_interval_seconds: float, timeout_seconds: float, mode: str) -> None:
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        response = _get_textract_job_status(textract_client=textract_client, job_id=job_id, mode=mode)
        status = str(response.get("JobStatus") or "").strip().upper()
        if status in TERMINAL_JOB_STATUSES:
            if status == "FAILED":
                message = str(response.get("StatusMessage") or "Textract job failed.").strip()
                raise InvalidFileContentError(message)
            return
        time.sleep(poll_interval_seconds)
    raise TimeoutError(f"OCR timeout after {timeout_seconds:.1f}s while waiting for processing.")


def _fetch_textract_result(*, textract_client, job_id: str, mode: str) -> tuple[int, list[dict[str, object]], dict[str, object]]:
    blocks: list[dict[str, object]] = []
    next_token: str | None = None
    page_count = 0
    document_metadata: dict[str, object] = {}
    while True:
        params: dict[str, object] = {"JobId": job_id, "MaxResults": 1000}
        if next_token:
            params["NextToken"] = next_token
        response = _get_textract_result_page(textract_client=textract_client, mode=mode, params=params)
        status = str(response.get("JobStatus") or "").strip().upper()
        if status == "FAILED":
            message = str(response.get("StatusMessage") or "Textract job failed.").strip()
            raise InvalidFileContentError(message)
        if status not in {"SUCCEEDED", "PARTIAL_SUCCESS"}:
            raise InvalidFileContentError("OCR job is not ready to fetch results.")
        document_metadata = response.get("DocumentMetadata") or document_metadata
        page_count = max(page_count, int(document_metadata.get("Pages") or 0))
        blocks.extend(response.get("Blocks") or [])
        next_token = response.get("NextToken")
        if not next_token:
            break
    return page_count, blocks, document_metadata


def _start_textract_job(*, textract_client, mode: str, bucket: str, s3_key: str, feature_types: tuple[str, ...]):
    document_location = {"S3Object": {"Bucket": bucket, "Name": s3_key}}
    if mode == TEXTRACT_ANALYSIS_MODE:
        return textract_client.start_document_analysis(
            DocumentLocation=document_location,
            FeatureTypes=list(feature_types),
        )
    return textract_client.start_document_text_detection(DocumentLocation=document_location)


def _get_textract_job_status(*, textract_client, job_id: str, mode: str) -> dict[str, object]:
    if mode == TEXTRACT_ANALYSIS_MODE:
        return textract_client.get_document_analysis(JobId=job_id, MaxResults=1)
    return textract_client.get_document_text_detection(JobId=job_id, MaxResults=1)


def _get_textract_result_page(*, textract_client, mode: str, params: dict[str, object]) -> dict[str, object]:
    if mode == TEXTRACT_ANALYSIS_MODE:
        return textract_client.get_document_analysis(**params)
    return textract_client.get_document_text_detection(**params)


def _resolve_feature_types() -> tuple[str, ...]:
    raw = os.getenv("TEXTRACT_FEATURE_TYPES", "").strip()
    if not raw:
        return DEFAULT_FEATURE_TYPES
    parts = [item.strip().upper() for item in raw.split(",") if item.strip()]
    if not parts:
        return DEFAULT_FEATURE_TYPES
    return tuple(parts)


def _resolve_textract_mode(explicit: str | None) -> str:
    raw = (explicit or os.getenv("TEXTRACT_MODE") or "").strip().lower()
    if raw == TEXTRACT_ANALYSIS_MODE:
        return TEXTRACT_ANALYSIS_MODE
    return TEXTRACT_TEXT_MODE


def _to_float_env(explicit: float | None, key: str, *, default: float, min_value: float) -> float:
    if explicit is not None:
        return max(min_value, float(explicit))
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(min_value, value)


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - started_at) * 1000, 3)
