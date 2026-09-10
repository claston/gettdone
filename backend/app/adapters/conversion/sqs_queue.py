from __future__ import annotations

import json
import re
from typing import Any

_JOB_ID_PATTERN = re.compile(r"^job_[A-Za-z0-9_-]{1,64}$")
_BATCH_ID_PATTERN = re.compile(r"^batch_[A-Za-z0-9_-]{1,64}$")
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class SqsConversionQueuePublisher:
    def __init__(
        self,
        *,
        queue_url: str,
        region: str | None = None,
        sqs_client: Any | None = None,
    ) -> None:
        self.queue_url = (queue_url or "").strip()
        if not self.queue_url:
            raise ValueError("Conversion SQS queue URL is required.")
        self.region = (region or "").strip() or None
        self._sqs_client = sqs_client

    def publish(self, *, job_id: str, batch_id: str, trace_id: str) -> str:
        if _JOB_ID_PATTERN.fullmatch(job_id or "") is None:
            raise ValueError("Invalid conversion job id.")
        if _BATCH_ID_PATTERN.fullmatch(batch_id or "") is None:
            raise ValueError("Invalid conversion batch id.")
        if _TRACE_ID_PATTERN.fullmatch(trace_id or "") is None:
            raise ValueError("Invalid conversion trace id.")
        body = {
            "schema_version": 1,
            "event_type": "conversion.requested",
            "job_id": job_id,
            "batch_id": batch_id,
            "trace_id": trace_id,
        }
        response = self._client().send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(body, separators=(",", ":"), sort_keys=True),
        )
        message_id = str(response.get("MessageId") or "").strip()
        if not message_id:
            raise RuntimeError("SQS did not return a message id.")
        return message_id

    def _client(self):
        if self._sqs_client is None:
            try:
                import boto3
            except Exception as exc:  # pragma: no cover - installed in production
                raise RuntimeError("SQS conversion publishing requires boto3.") from exc
            self._sqs_client = boto3.session.Session(region_name=self.region).client("sqs")
        return self._sqs_client
