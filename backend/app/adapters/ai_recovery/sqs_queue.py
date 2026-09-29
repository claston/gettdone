from __future__ import annotations

import json
from typing import Any

from app.application.ai_recovery.queue import AIRecoveryQueueMessage


class SqsAIRecoveryQueuePublisher:
    def __init__(
        self,
        *,
        queue_url: str,
        region: str | None = None,
        sqs_client: Any | None = None,
    ) -> None:
        self.queue_url = str(queue_url or "").strip()
        if not self.queue_url:
            raise ValueError("AI recovery SQS queue URL is required.")
        self.region = str(region or "").strip() or None
        self._sqs_client = sqs_client

    def publish(self, message: AIRecoveryQueueMessage) -> str:
        response = self._client().send_message(
            QueueUrl=self.queue_url,
            MessageBody=json.dumps(
                message.model_dump(mode="json"),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        message_id = str(response.get("MessageId") or "").strip()
        if not message_id:
            raise RuntimeError("AI recovery SQS publish returned no MessageId.")
        return message_id

    def _client(self):
        if self._sqs_client is None:
            try:
                import boto3
            except Exception as exc:  # pragma: no cover - production dependency
                raise RuntimeError("AI recovery SQS publishing requires boto3.") from exc
            self._sqs_client = boto3.session.Session(region_name=self.region).client("sqs")
        return self._sqs_client
