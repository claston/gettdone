"""Whole-document paid analysis, pagination, cost boundaries and input lifetime."""

from io import BytesIO

import pytest
from botocore.exceptions import ClientError
from pypdf import PdfReader

from app.application.asynchronous_textract import AsynchronousTextractReader
from app.application.errors import InvalidFileContentError
from tests.test_conversion_pro import pdf

TOKEN = "a" * 64


class S3:
    def __init__(self):
        self.uploads, self.deletes = [], []

    def put_object(self, **kwargs):
        self.uploads.append(kwargs)
        return {"VersionId": "owned-version"}

    def delete_object(self, **kwargs):
        self.deletes.append(kwargs)


def completed(pages=2, **kwargs):
    return {
        "JobStatus": "SUCCEEDED",
        "DocumentMetadata": {"Pages": pages},
        "Blocks": [{"Id": f"page-{n}", "BlockType": "PAGE", "Page": n} for n in range(1, pages + 1)],
        **kwargs,
    }


class Textract:
    def __init__(self, responses):
        self.responses, self.starts, self.gets = list(responses), [], []

    def start_document_analysis(self, **kwargs):
        self.starts.append(kwargs)
        return {"JobId": "provider-job"}

    def get_document_analysis(self, **kwargs):
        self.gets.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def reader(s3, textract, **kwargs):
    return AsynchronousTextractReader(bucket="test", s3_client=s3, textract_client=textract, poll_interval_seconds=0, **kwargs)


def test_one_original_pdf_one_start_and_all_result_pages_without_sync_calls():
    s3 = S3()
    first = completed()
    first["Blocks"] += [
        {"Id": "line-1", "BlockType": "LINE", "Page": 1, "Text": "one", "Relationships": [{"Type": "CHILD", "Ids": ["word-1"]}]},
        {"Id": "word-1", "BlockType": "WORD", "Page": 1, "Text": "one"},
    ]
    first["NextToken"] = "next"
    second = completed()
    second.pop("DocumentMetadata")  # Optional metadata need not repeat on every result page.
    second["Blocks"] = [
        {"Id": "line-2", "BlockType": "LINE", "Page": 2, "Text": "two", "Relationships": [{"Type": "CHILD", "Ids": ["word-2"]}]},
        {"Id": "word-2", "BlockType": "WORD", "Page": 2, "Text": "two"},
    ]
    textract = Textract([{"JobStatus": "IN_PROGRESS"}, first, second])
    attempted, progress = [], []
    raw = pdf(2)
    result = reader(s3, textract).read(
        raw,
        max_pages=2,
        request_token=TOKEN,
        before_document=attempted.append,
        on_progress=lambda page, total: progress.append((page, total)),
    )
    assert attempted == [2] and progress == [(1, 2), (2, 2)]
    assert len(s3.uploads) == 1 and s3.uploads[0]["Body"] == raw
    assert len(PdfReader(BytesIO(s3.uploads[0]["Body"])).pages) == 2
    assert len(textract.starts) == 1 and textract.starts[0]["ClientRequestToken"] == TOKEN
    assert textract.starts[0]["FeatureTypes"] == ["TABLES", "LAYOUT"]
    assert textract.starts[0]["DocumentLocation"]["S3Object"]["Version"] == "owned-version"
    assert [item.get("NextToken") for item in textract.gets] == [None, None, "next"]
    assert [page.lines[0].word_ids for page in result.pages] == [("word-1",), ("word-2",)]
    assert result.metrics["textract_api_style"] == "async"
    assert result.operation_id == "provider-job"
    assert s3.deletes == [{"Bucket": "test", "Key": s3.uploads[0]["Key"], "VersionId": "owned-version"}]


def test_definite_start_rejection_releases_whole_document_and_cleans_input():
    class Denied(Textract):
        def start_document_analysis(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "private"}}, "StartDocumentAnalysis")

    s3, rejected = S3(), []
    with pytest.raises(InvalidFileContentError):
        reader(s3, Denied([])).read(pdf(2), max_pages=2, request_token=TOKEN, on_rejected_document=rejected.append)
    assert rejected == [2] and len(s3.deletes) == 1


def test_poll_failure_retains_input_for_an_already_started_job():
    error = ClientError({"Error": {"Code": "AccessDeniedException"}}, "GetDocumentAnalysis")
    s3, textract = S3(), Textract([error])
    with pytest.raises(InvalidFileContentError) as caught:
        reader(s3, textract).read(pdf(2), max_pages=2, request_token=TOKEN)
    assert not s3.deletes
    assert caught.value._parse_observability["ocr_provider_operation"] == "GetDocumentAnalysis"


@pytest.mark.parametrize(
    "response",
    [
        {"JobStatus": "FAILED"},
        {**completed(), "JobStatus": "PARTIAL_SUCCESS"},
        completed(1),
        {**completed(), "Blocks": [{"Id": "page-1", "BlockType": "PAGE", "Page": 1}]},
        {**completed(), "Warnings": [{"ErrorCode": "SOME_PAGES_FAILED", "Pages": [2]}]},
    ],
)
def test_incomplete_or_failed_provider_result_never_becomes_complete_extraction(response):
    s3 = S3()
    with pytest.raises(InvalidFileContentError):
        reader(s3, Textract([response])).read(pdf(2), max_pages=2, request_token=TOKEN)
    assert len(s3.deletes) == 1


def test_timeout_does_not_delete_input_of_a_running_job(monkeypatch):
    from app.application import asynchronous_textract

    times = iter([0, 2])
    monkeypatch.setattr(asynchronous_textract.time, "monotonic", lambda: next(times))
    s3 = S3()
    with pytest.raises(InvalidFileContentError):
        reader(s3, Textract([]), timeout_seconds=1).read(pdf(2), max_pages=2, request_token=TOKEN)
    assert not s3.deletes
