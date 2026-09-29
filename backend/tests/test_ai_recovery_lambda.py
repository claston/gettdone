from __future__ import annotations

import json
from types import SimpleNamespace

from app.application.ai_recovery.queue import AIRecoveryQueueMessage
from app.workers import ai_recovery_lambda


class _Processor:
    def __init__(self, *, fail_key: str | None = None) -> None:
        self.fail_key = fail_key
        self.calls: list[dict[str, str]] = []

    def process(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["expected_idempotency_key"] == self.fail_key:
            raise RuntimeError("retry")
        return SimpleNamespace(status="completed")


def test_ai_recovery_lambda_returns_partial_batch_failures(monkeypatch) -> None:
    failing_key = "b" * 64
    processor = _Processor(fail_key=failing_key)
    monkeypatch.setattr(ai_recovery_lambda, "_processor", processor)

    def record(message_id: str, key: str) -> dict[str, object]:
        message = AIRecoveryQueueMessage(
            idempotency_key=key,
            bucket="private-ai-recovery",
            ready_key=f"ai-recovery/requests/v1/{key}/ready.json",
        )
        return {"messageId": message_id, "body": json.dumps(message.model_dump(mode="json"))}

    response = ai_recovery_lambda.lambda_handler(
        {"Records": [record("ok", "a" * 64), record("retry", failing_key)]},
        SimpleNamespace(aws_request_id="request-1"),
    )

    assert response == {"batchItemFailures": [{"itemIdentifier": "retry"}]}
    assert len(processor.calls) == 2
