import pytest

from app.application.errors import InvalidFileContentError
from app.application.textract_gateway import TextractGateway


class _FakeS3Client:
    def __init__(self) -> None:
        self.upload_calls = 0
        self.delete_calls = 0

    def upload_fileobj(self, fileobj, bucket: str, key: str) -> None:
        _ = fileobj
        _ = bucket
        _ = key
        self.upload_calls += 1

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        _ = Bucket
        _ = Key
        self.delete_calls += 1


class _FakeTextractClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.poll_calls = 0
        self.fetch_calls = 0
        self.start_analysis_calls = 0
        self.start_text_calls = 0

    def start_document_analysis(self, **kwargs):
        _ = kwargs
        self.start_analysis_calls += 1
        return {"JobId": "job-123"}

    def start_document_text_detection(self, **kwargs):
        _ = kwargs
        self.start_text_calls += 1
        return {"JobId": "job-123"}

    def get_document_analysis(self, **kwargs):
        _ = kwargs
        max_results = int(kwargs.get("MaxResults", 0) or 0)
        next_token = kwargs.get("NextToken")
        if max_results == 1:
            self.poll_calls += 1
            if self.fail:
                return {"JobStatus": "FAILED", "StatusMessage": "failed on provider"}
            return {"JobStatus": "SUCCEEDED"}

        self.fetch_calls += 1
        if next_token is None:
            return {
                "JobStatus": "SUCCEEDED",
                "DocumentMetadata": {"Pages": 2},
                "Blocks": [{"BlockType": "LINE", "Id": "l1", "Text": "a"}],
                "NextToken": "token-2",
            }
        return {
            "JobStatus": "SUCCEEDED",
            "DocumentMetadata": {"Pages": 2},
            "Blocks": [{"BlockType": "LINE", "Id": "l2", "Text": "b"}],
        }

    def get_document_text_detection(self, **kwargs):
        return self.get_document_analysis(**kwargs)


class _FakeSession:
    def __init__(self, *, s3: _FakeS3Client, textract: _FakeTextractClient) -> None:
        self._s3 = s3
        self._textract = textract

    def client(self, service_name: str):
        if service_name == "s3":
            return self._s3
        if service_name == "textract":
            return self._textract
        raise AssertionError("unexpected service")


class _FakeBoto3:
    def __init__(self, *, s3: _FakeS3Client, textract: _FakeTextractClient) -> None:
        self._s3 = s3
        self._textract = textract

        class _SessionFactory:
            def __init__(self, s3_client: _FakeS3Client, textract_client: _FakeTextractClient) -> None:
                self.s3_client = s3_client
                self.textract_client = textract_client

            def Session(self, region_name=None):
                _ = region_name
                return _FakeSession(s3=self.s3_client, textract=self.textract_client)

        self.session = _SessionFactory(self._s3, self._textract)


def test_gateway_fetches_paginated_blocks_and_deletes_s3_object(monkeypatch) -> None:
    s3 = _FakeS3Client()
    textract = _FakeTextractClient()
    fake_boto3 = _FakeBoto3(s3=s3, textract=textract)
    monkeypatch.setattr("app.application.textract_gateway._load_boto3", lambda: fake_boto3)

    gateway = TextractGateway(bucket="test-bucket", region="us-east-1", poll_interval_seconds=0.01, timeout_seconds=3.0)
    result = gateway.analyze_pdf(raw_bytes=b"%PDF synthetic")

    assert result["provider"] == "aws_textract"
    assert result["page_count"] == 2
    assert len(result["blocks"]) == 2
    assert s3.upload_calls == 1
    assert s3.delete_calls == 1
    assert textract.start_text_calls == 1
    assert textract.start_analysis_calls == 0
    assert result["metrics"]["textract_mode"] == "text"


def test_gateway_uses_analysis_mode_when_requested(monkeypatch) -> None:
    s3 = _FakeS3Client()
    textract = _FakeTextractClient()
    fake_boto3 = _FakeBoto3(s3=s3, textract=textract)
    monkeypatch.setattr("app.application.textract_gateway._load_boto3", lambda: fake_boto3)

    gateway = TextractGateway(
        bucket="test-bucket",
        region="us-east-1",
        poll_interval_seconds=0.01,
        timeout_seconds=3.0,
        mode="analysis",
    )
    result = gateway.analyze_pdf(raw_bytes=b"%PDF synthetic")

    assert result["provider"] == "aws_textract"
    assert textract.start_analysis_calls == 1
    assert textract.start_text_calls == 0
    assert result["metrics"]["textract_mode"] == "analysis"


def test_gateway_deletes_s3_object_even_when_textract_job_fails(monkeypatch) -> None:
    s3 = _FakeS3Client()
    textract = _FakeTextractClient(fail=True)
    fake_boto3 = _FakeBoto3(s3=s3, textract=textract)
    monkeypatch.setattr("app.application.textract_gateway._load_boto3", lambda: fake_boto3)

    gateway = TextractGateway(bucket="test-bucket", region="us-east-1", poll_interval_seconds=0.01, timeout_seconds=3.0)
    with pytest.raises(InvalidFileContentError):
        gateway.analyze_pdf(raw_bytes=b"%PDF synthetic")
    assert s3.upload_calls == 1
    assert s3.delete_calls == 1
