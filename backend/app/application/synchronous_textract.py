"""Explicit per-request TABLES/LAYOUT reader; never changes the default OCR mode."""

import hashlib
import logging
import os
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from uuid import uuid4

from botocore.config import Config
from pypdf import PdfReader, PdfWriter

from app.application.textract_extraction_mapper import map_textract_blocks_to_extraction
from app.application.textract_gateway import _load_boto3

logger = logging.getLogger(__name__)


class SynchronousTextractReader:
    def __init__(self, *, bucket=None, s3_client=None, textract_client=None):
        self.bucket = bucket or os.getenv("TEXTRACT_TEMP_BUCKET", "")
        self.s3 = s3_client
        self.textract = textract_client

    def read(self, raw, *, max_pages, before_page=None, on_progress=None, on_rejected_page=None):
        document = PdfReader(BytesIO(raw))
        if document.is_encrypted or not 1 <= len(document.pages) <= max_pages:
            raise ValueError("PDF fora dos limites da Conversão PRO.")
        pages = []
        for page in document.pages:
            output = BytesIO()
            writer = PdfWriter()
            writer.add_page(page)
            writer.write(output)
            content = output.getvalue()
            if len(content) > 10 * 1024 * 1024:
                raise ValueError("Uma página excede o limite de 10 MB da Conversão PRO.")
            pages.append(content)
        if not self.bucket:
            raise ValueError("A Conversão PRO não está configurada neste ambiente.")
        if self.s3 is None or self.textract is None:
            session = _load_boto3().session.Session(region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1")
            self.s3 = self.s3 or session.client("s3")
            self.textract = self.textract or session.client(
                "textract", config=Config(retries={"total_max_attempts": 1}, read_timeout=60, connect_timeout=10)
            )
        blocks = []
        prefix = os.getenv("TEXTRACT_S3_PREFIX", "textract/tmp").strip("/")
        run_id = uuid4().hex
        for number, content in enumerate(pages, 1):
            key = f"{prefix}/{run_id}/page-{number}.pdf"
            uploaded = self.s3.put_object(Bucket=self.bucket, Key=key, Body=content, ContentType="application/pdf")
            try:
                if before_page:
                    before_page(number)
                try:
                    response = self.textract.analyze_document(
                        Document={"S3Object": {"Bucket": self.bucket, "Name": key}}, FeatureTypes=["TABLES", "LAYOUT"]
                    )
                except Exception as exc:
                    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                    if code in {
                        "AccessDeniedException",
                        "InvalidParameterException",
                        "InvalidS3ObjectException",
                        "UnsupportedDocumentException",
                    }:
                        if on_rejected_page:
                            on_rejected_page(number)
                    raise
                page_blocks = deepcopy(response["Blocks"])
                for block in page_blocks:
                    block["Page"] = number
                    if block.get("Id"):
                        block["Id"] = f"{number}:" + block["Id"]
                    for relation in block.get("Relationships", []):
                        relation["Ids"] = [f"{number}:" + identifier for identifier in relation.get("Ids", [])]
                blocks.extend(page_blocks)
                if on_progress:
                    on_progress(number, len(pages))
            finally:
                delete = {"Bucket": self.bucket, "Key": key}
                if uploaded.get("VersionId"):
                    delete["VersionId"] = uploaded["VersionId"]
                try:
                    self.s3.delete_object(**delete)
                except Exception:
                    logger.warning("conversion_pro_temporary_cleanup_failed")
        extraction = map_textract_blocks_to_extraction(document_hash=hashlib.sha256(raw).hexdigest(), blocks=blocks, page_count=len(pages))
        return replace(
            extraction, metrics={**extraction.metrics, "textract_mode": "analysis", "textract_api_style": "sync", "conversion_mode": "pro"}
        )
