"""Dedicated PRO fiscal storage/queue adapters using the private AWS runtime."""

import hashlib
import json
import os
import re
from io import BytesIO
from uuid import UUID, uuid4

from pypdf import PdfReader

from app.adapters.conversion.canonical_movements import PostgresMovementRepository
from app.adapters.conversion.direct_upload import S3DirectUploadService
from app.adapters.conversion.sqs_queue import SqsConversionQueuePublisher
from app.application.pro_fiscal_documents import MAX_FISCAL_BYTES, parse_fiscal_pages, parse_fiscal_xml
from app.application.pro_fiscal_service import ProFiscalService
from app.application.runtime_environment import read_bool_env


class FiscalObjects:
    def __init__(self, uploads):
        self.uploads = uploads

    def reference(self, filename, size_bytes, digest):
        name = self.uploads._safe_filename(filename)
        kind = name.rsplit(".", 1)[-1].lower()
        if kind not in {"xml", "pdf"} or not 1 <= size_bytes <= MAX_FISCAL_BYTES or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Envie uma nota XML ou PDF de até 5 MB.")
        return {
            "storage_key": "doc_" + uuid4().hex[:24],
            "filename": name,
            "file_type": kind,
            "size_bytes": size_bytes,
            "sha256_hex": digest,
        }

    def _key(self, reference):
        if not re.fullmatch(r"doc_[a-f0-9]{24}", reference["storage_key"]) or reference["file_type"] not in {"xml", "pdf"}:
            raise ValueError("Referência fiscal inválida.")
        return f"{self.uploads.prefix}/{reference['storage_key']}/document.{reference['file_type']}"

    def prepare(self, reference):
        # Keep the converter's extension/content-type allowlist unchanged.
        fields = {
            "Content-Type": "application/xml" if reference["file_type"] == "xml" else "application/pdf",
            "x-amz-meta-sha256": reference["sha256_hex"],
        }
        if self.uploads.server_side_encryption:
            fields["x-amz-server-side-encryption"] = self.uploads.server_side_encryption
        if self.uploads.kms_key_id:
            fields["x-amz-server-side-encryption-aws-kms-key-id"] = self.uploads.kms_key_id
        result = self.uploads._client().generate_presigned_post(
            Bucket=self.uploads.bucket,
            Key=self._key(reference),
            Fields=fields,
            Conditions=[
                {"Content-Type": fields["Content-Type"]},
                *[{k: v} for k, v in fields.items() if k != "Content-Type"],
                ["content-length-range", reference["size_bytes"], reference["size_bytes"]],
            ],
            ExpiresIn=300,
        )
        return {"url": result["url"], "fields": result["fields"], "content_type": fields["Content-Type"]}

    def verify(self, reference):
        result = self.uploads._client().head_object(Bucket=self.uploads.bucket, Key=self._key(reference))
        if (
            result.get("ContentLength") != reference["size_bytes"]
            or (result.get("Metadata") or {}).get("sha256") != reference["sha256_hex"]
        ):
            raise ValueError("O arquivo enviado não corresponde ao declarado.")

    def read(self, reference):
        result = self.uploads._client().get_object(Bucket=self.uploads.bucket, Key=self._key(reference))
        stream = result["Body"]
        try:
            raw = stream.read(MAX_FISCAL_BYTES + 1)
        finally:
            stream.close()
        if len(raw) != reference["size_bytes"] or hashlib.sha256(raw).hexdigest() != reference["sha256_hex"]:
            raise ValueError("O conteúdo do arquivo mudou após o envio. Reenvie a nota.")
        return raw

    def delete(self, reference):
        self.uploads._client().delete_object(Bucket=self.uploads.bucket, Key=self._key(reference))


class FiscalQueue:
    def __init__(self, queue):
        self.queue = queue

    def publish(self, identifier):
        identifier = str(UUID(identifier))
        result = self.queue._client().send_message(
            QueueUrl=self.queue.queue_url,
            MessageBody=json.dumps({"schema_version": 1, "event_type": "pro.fiscal.requested", "import_id": identifier}),
        )
        if not result.get("MessageId"):
            raise ConnectionError("Fiscal queue did not acknowledge the message")


def extract_fiscal(raw, kind):
    if kind == "xml":
        return parse_fiscal_xml(raw)
    try:
        pdf = PdfReader(BytesIO(raw))
        if pdf.is_encrypted or not 1 <= len(pdf.pages) <= 10:
            raise ValueError("Envie uma nota PDF sem senha, com até 10 páginas.")
        pages = [p.extract_text() or "" for p in pdf.pages]
    except Exception:
        raise ValueError("Não foi possível ler o PDF fiscal. Confira o arquivo e o limite de 10 páginas.") from None
    if any(len(p.strip()) < 40 for p in pages):
        if not read_bool_env("PRO_FISCAL_OCR_ENABLED", default=False):
            raise ValueError("Esta nota precisa de OCR. Envie o XML ou habilite o OCR fiscal no PRO.")
        from app.application.textract_gateway import TextractGateway

        result = TextractGateway(timeout_seconds=480).analyze_pdf(raw_bytes=raw)
        pages = [
            "\n".join(b["Text"] for b in result["blocks"] if b.get("BlockType") == "LINE" and b.get("Page") == i)
            for i in range(1, len(pdf.pages) + 1)
        ]
        if any(not page.strip() for page in pages):
            raise ValueError("Há páginas sem leitura no OCR fiscal. Confira o documento inteiro.")
    return parse_fiscal_pages(pages)


def build_pro_fiscal_service(*, storage=True):
    repo = PostgresMovementRepository(os.environ["DATABASE_URL"], schema=os.getenv("DATABASE_SCHEMA", "public"))
    if not storage:
        return ProFiscalService(repo)
    region = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    objects = FiscalObjects(
        S3DirectUploadService(
            bucket=os.environ["CONVERSION_S3_BUCKET"],
            prefix=os.getenv("CONVERSION_S3_PREFIX", "conversion/jobs") + "/pro-fiscal",
            region=region,
            server_side_encryption=os.getenv("CONVERSION_S3_SERVER_SIDE_ENCRYPTION", "AES256"),
            kms_key_id=os.getenv("CONVERSION_S3_KMS_KEY_ID"),
        )
    )
    queue = FiscalQueue(SqsConversionQueuePublisher(queue_url=os.environ["CONVERSION_SQS_QUEUE_URL"], region=region))
    return ProFiscalService(
        repo,
        objects=objects,
        queue=queue,
        ttl_seconds=min(int(os.getenv("ANALYSIS_TTL_SECONDS", "86400")), int(os.getenv("CONVERSION_JOB_ACTIVE_TTL_SECONDS", "86400"))),
    )


def process_fiscal_message(body):
    if not read_bool_env("PRO_FISCAL_ENABLED", default=False):
        raise RuntimeError("Fiscal imports are disabled")
    build_pro_fiscal_service().complete_import(str(UUID(body["import_id"])), extract_fiscal)


def fiscal_maintenance():
    # Maintenance remains enabled on rollback so retention is never extended.
    service = build_pro_fiscal_service()
    removed = service.cleanup()
    sent = service.dispatch() if read_bool_env("PRO_FISCAL_ENABLED", default=False) else 0
    return {"fiscal_sources_removed": removed, "fiscal_messages_published": sent}
