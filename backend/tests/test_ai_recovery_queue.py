from __future__ import annotations

import json

from app.adapters.ai_recovery.sqs_queue import SqsAIRecoveryQueuePublisher
from app.application.ai_recovery.queue import AIRecoveryQueueMessage


class _Sqs:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def send_message(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": "msg-123"}


def test_ai_recovery_queue_message_contains_references_not_pdf_or_transactions() -> None:
    client = _Sqs()
    publisher = SqsAIRecoveryQueuePublisher(
        queue_url="https://sqs.us-east-1.amazonaws.com/123/ai-recovery",
        sqs_client=client,
    )
    message = AIRecoveryQueueMessage(
        idempotency_key="a" * 64,
        bucket="private-ai-recovery",
        ready_key="ai-recovery/requests/v1/2026-09-28/key/ready.json",
    )

    message_id = publisher.publish(message)

    assert message_id == "msg-123"
    body = json.loads(client.calls[0]["MessageBody"])
    assert body == message.model_dump(mode="json")
    assert "pdf" not in body
    assert "transactions" not in body
