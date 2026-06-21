import logging
import os
import tempfile
from hashlib import sha256
from pathlib import Path

from fastapi import UploadFile

from app.application import FileTooLargeError
from app.application.conversion import document_preflight_service as document_preflight_service_module
from app.application.conversion.document_conversion_pipeline import StagedUploadRef

logger = logging.getLogger(__name__)
UPLOAD_READ_CHUNK_SIZE_BYTES = 1024 * 1024
UPLOAD_STAGING_MAX_BYTES = document_preflight_service_module.TEXT_PDF_MAX_UPLOAD_SIZE_BYTES
StagedUpload = StagedUploadRef


def _resolve_upload_staging_dir() -> Path:
    staging_dir = Path(__file__).resolve().parents[3] / "tmp" / "upload_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    return staging_dir


def _build_file_too_large_error(max_upload_size_bytes: int) -> FileTooLargeError:
    exc = FileTooLargeError()
    setattr(exc, "_max_upload_size_bytes", int(max_upload_size_bytes))
    return exc


async def _stage_upload_to_temp_file(
    file: UploadFile,
    *,
    max_bytes: int = UPLOAD_STAGING_MAX_BYTES,
) -> StagedUpload:
    staging_dir = _resolve_upload_staging_dir()
    fd, temp_path = tempfile.mkstemp(
        prefix="convert-upload-",
        suffix=Path(file.filename or "").suffix or ".bin",
        dir=staging_dir,
    )
    staged_path = Path(temp_path)
    total = 0
    digest = sha256()
    try:
        with os.fdopen(fd, "wb") as handle:
            while chunk := await file.read(UPLOAD_READ_CHUNK_SIZE_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise _build_file_too_large_error(max_bytes)
                digest.update(chunk)
                handle.write(chunk)
        return StagedUpload(path=staged_path, size_bytes=total, sha256_hex=digest.hexdigest())
    except Exception:
        try:
            staged_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove staged upload file %s", staged_path, exc_info=True)
        raise


def _cleanup_staged_upload(staged_upload: StagedUpload | None) -> None:
    if staged_upload is None:
        return
    try:
        staged_upload.path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Failed to remove staged upload file %s", staged_upload.path, exc_info=True)


def _read_staged_upload_bytes(staged_upload: StagedUpload) -> bytes:
    return staged_upload.path.read_bytes()
