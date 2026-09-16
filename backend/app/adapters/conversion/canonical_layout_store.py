from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from app.application.conversion.canonical_layout_capture import (
    CanonicalLayoutArtifact,
    CanonicalLayoutCaptureService,
    CanonicalLayoutGenerator,
)
from app.application.pdf_ocr import (
    extract_pdf_first_page_header_text_with_ocr,
    extract_pdf_page_texts_with_ocr,
)
from app.application.pdf_parser import is_textract_enabled
from app.application.textract_header_ocr import extract_pdf_first_page_header_text_with_textract
from app.application.textract_layout_preview import extract_pdf_layout_preview_with_textract

_CAPTURE_ID_PATTERN = re.compile(r"^cap_[a-f0-9]{24}$")


class S3CanonicalLayoutStore:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "conversion/canonical-layouts/candidates/v1",
        region: str | None = None,
        s3_client: Any | None = None,
    ) -> None:
        self.bucket = str(bucket or "").strip()
        if not self.bucket:
            raise ValueError("Canonical layout S3 bucket is required.")
        self.prefix = str(prefix or "").strip().strip("/")
        self.region = str(region or "").strip() or None
        self._s3_client = s3_client

    def store(self, artifact: CanonicalLayoutArtifact) -> None:
        if not artifact.privacy_validated:
            raise ValueError("Canonical artifact did not pass privacy validation.")
        if _CAPTURE_ID_PATTERN.fullmatch(artifact.capture_id) is None:
            raise ValueError("Invalid canonical capture id.")
        bank_partition = _bank_partition(artifact.manifest)
        relative_key = f"bank={bank_partition}/{artifact.capture_id}"
        base_key = f"{self.prefix}/{relative_key}" if self.prefix else relative_key
        metadata = {
            "schema-version": str(artifact.manifest.get("schema_version") or "unknown")[:20],
            "privacy-validation-version": str(
                artifact.manifest.get("privacy_validation_version") or "unknown"
            )[:20],
            "bank-partition": bank_partition,
        }
        self._client().put_object(
            Bucket=self.bucket,
            Key=f"{base_key}/canonical.pdf",
            Body=artifact.pdf_bytes,
            ContentLength=len(artifact.pdf_bytes),
            ContentType="application/pdf",
            Metadata=metadata,
            ServerSideEncryption="AES256",
        )
        manifest_bytes = json.dumps(
            artifact.manifest,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._client().put_object(
            Bucket=self.bucket,
            Key=f"{base_key}/layout.json",
            Body=manifest_bytes,
            ContentLength=len(manifest_bytes),
            ContentType="application/json",
            Metadata=metadata,
            ServerSideEncryption="AES256",
        )

    def _client(self):
        if self._s3_client is None:
            try:
                import boto3
            except Exception as exc:  # pragma: no cover - production dependency
                raise RuntimeError("Canonical layout S3 storage requires boto3.") from exc
            self._s3_client = boto3.session.Session(region_name=self.region).client("s3")
        return self._s3_client


def _bank_partition(manifest: dict[str, object]) -> str:
    bank = manifest.get("bank")
    if not isinstance(bank, dict):
        return "unknown"
    code = str(bank.get("code") or "").strip()
    if re.fullmatch(r"\d{3}", code):
        return code
    decomposed = unicodedata.normalize("NFKD", str(bank.get("name") or ""))
    ascii_name = "".join(character for character in decomposed if not unicodedata.combining(character))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")[:80].rstrip("-")
    return slug or "unknown"


def build_s3_canonical_layout_capture_service(
    *,
    enabled: bool,
    bucket: str,
    prefix: str = "conversion/canonical-layouts/candidates/v1",
    region: str | None = None,
    max_pages: int = 20,
    max_extracted_chars: int = 250_000,
    bank_header_ocr_enabled: bool = False,
    failure_capture_enabled: bool = False,
    v2_enabled: bool = False,
) -> CanonicalLayoutCaptureService:
    if not enabled:
        return CanonicalLayoutCaptureService(enabled=False)
    textract_enabled = is_textract_enabled()
    return CanonicalLayoutCaptureService(
        enabled=True,
        generator=CanonicalLayoutGenerator(
            max_pages=max_pages,
            max_extracted_chars=max_extracted_chars,
            ocr_page_text_extractor=extract_pdf_page_texts_with_ocr,
            layout_preview_extractor=(extract_pdf_layout_preview_with_textract if v2_enabled and textract_enabled else None),
            bank_header_ocr_enabled=bank_header_ocr_enabled,
            bank_header_ocr_extractor=(
                extract_pdf_first_page_header_text_with_textract
                if textract_enabled
                else extract_pdf_first_page_header_text_with_ocr
            ),
            bank_header_ocr_provider="aws_textract" if textract_enabled else "local",
            schema_version="2" if v2_enabled else "1",
        ),
        store=S3CanonicalLayoutStore(
            bucket=bucket,
            prefix=_v2_prefix(prefix) if v2_enabled else prefix,
            region=region,
        ),
        failure_capture_enabled=failure_capture_enabled,
    )


def _v2_prefix(prefix: str) -> str:
    clean_prefix = str(prefix or "").strip().strip("/")
    if clean_prefix.endswith("/v2"):
        return clean_prefix
    if clean_prefix.endswith("/v1"):
        return f"{clean_prefix[:-3]}/v2"
    return f"{clean_prefix}/v2" if clean_prefix else "v2"
