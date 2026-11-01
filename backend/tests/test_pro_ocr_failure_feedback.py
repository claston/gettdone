"""Diagnose paid OCR failures without exposing provider details or retrying them."""

import pytest
from botocore.exceptions import ClientError

from app.application.asynchronous_textract import AsynchronousTextractReader
from app.application.conversion.document_conversion_pipeline import (
    _build_failure_diagnostics,
    _resolve_error_observability,
    _resolve_failed_conversion_code,
)
from app.application.errors import InvalidFileContentError
from tests.test_conversion_pro import pdf


def test_missing_bucket_reports_configuration_failure_before_cloud_access(monkeypatch):
    monkeypatch.delenv("TEXTRACT_TEMP_BUCKET", raising=False)
    with pytest.raises(InvalidFileContentError, match="configurado") as caught:
        AsynchronousTextractReader(s3_client=object(), textract_client=object()).read(pdf(1), max_pages=1, request_token="a" * 64)
    assert caught.value._parse_observability["error_subcode"] == "pro_ocr_configuration"
    assert caught.value._parse_observability["textract_attempted"] == 0


@pytest.mark.parametrize("stage,operation", [("upload", "PutObject"), ("analysis", "StartDocumentAnalysis")])
def test_provider_failure_keeps_safe_diagnostics_and_cleanup(stage, operation):
    calls, rejected = [], []
    failure = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "private provider details"}}, operation)

    class S3:
        def put_object(self, **kwargs):
            calls.append("upload")
            if stage == "upload":
                raise failure
            return {"VersionId": "owned-version"}

        def delete_object(self, **kwargs):
            assert kwargs["VersionId"] == "owned-version"
            calls.append("cleanup")

    class Textract:
        def start_document_analysis(self, **kwargs):
            calls.append("analysis")
            raise failure

    with pytest.raises(InvalidFileContentError) as caught:
        AsynchronousTextractReader(bucket="private-bucket", s3_client=S3(), textract_client=Textract()).read(
            pdf(1), max_pages=1, request_token="a" * 64, on_rejected_document=rejected.append
        )
    assert "private" not in str(caught.value)
    metrics = caught.value._parse_observability
    assert metrics["error_stage"] == "ocr"
    assert metrics["error_subcode"] == "pro_ocr_" + stage
    assert metrics["ocr_provider_error_code"] == "AccessDeniedException"
    assert metrics["ocr_provider_operation"] == operation
    assert _resolve_error_observability(caught.value)[:2] == ("ocr", "pro_ocr_" + stage)
    assert _resolve_failed_conversion_code(caught.value) == "ocr_service_unavailable"
    diagnostics = _build_failure_diagnostics(caught.value)
    assert diagnostics["ocr_provider_error_code"] == "AccessDeniedException"
    assert diagnostics["ocr_provider_operation"] == operation
    assert calls == (["upload"] if stage == "upload" else ["upload", "analysis", "cleanup"])
    assert rejected == ([] if stage == "upload" else [1])


@pytest.mark.parametrize("prefix", ["", "/", "  "])
def test_empty_prefix_uses_the_existing_temporary_scope(prefix, monkeypatch):
    monkeypatch.setenv("TEXTRACT_S3_PREFIX", prefix)

    class S3:
        def put_object(self, **kwargs):
            assert kwargs["Key"].startswith("textract/tmp/")
            return {}

        def delete_object(self, **kwargs):
            pass

    class Textract:
        def start_document_analysis(self, **kwargs):
            return {"JobId": "job"}

        def get_document_analysis(self, **kwargs):
            return {"JobStatus": "SUCCEEDED", "DocumentMetadata": {"Pages": 1}, "Blocks": [{"Id": "p", "BlockType": "PAGE", "Page": 1}]}

    AsynchronousTextractReader(bucket="test", s3_client=S3(), textract_client=Textract()).read(pdf(1), max_pages=1, request_token="a" * 64)
