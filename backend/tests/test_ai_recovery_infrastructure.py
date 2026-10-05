from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "ai-recovery-shadow.yaml"


def test_ai_recovery_infrastructure_cannot_invoke_bedrock() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")

    assert "bedrock:InvokeModel" not in template
    assert "AWS::Lambda::EventSourceMapping" not in template
    assert "sqs:SendMessage" not in template
    assert 'AI_RECOVERY_BEDROCK_ENABLED: "false"' in template


def test_ai_recovery_request_objects_have_one_day_lifecycle_default() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    request_lifecycle = template.split("Id: ExpireOriginalRequests", maxsplit=1)[1].split(
        "Id: ExpireRestrictedResults", maxsplit=1
    )[0]

    assert "ExpirationInDays: 1" in request_lifecycle
    assert "ExpirationInDays: !Ref RequestRetentionDays" not in request_lifecycle
