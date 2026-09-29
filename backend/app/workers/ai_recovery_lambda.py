from __future__ import annotations

import logging
import os
import re

from app.adapters.ai_recovery.bedrock_nova_diagnostic import build_bedrock_nova_diagnostic_analyzer
from app.adapters.ai_recovery.s3_diagnostic_store import S3AIRecoveryObjectReader, S3AIRecoveryResultStore
from app.application.ai_recovery.config import AIRecoveryConfig
from app.application.ai_recovery.diagnostic_processing import AIRecoveryDiagnosticProcessor
from app.application.ai_recovery.errors import AIExtractionError
from app.application.ai_recovery.privacy_fixture import CanonicalV3PrivacyFixtureBuilder
from app.application.ai_recovery.queue import AIRecoveryQueueMessage

logger = logging.getLogger(__name__)
_processor: AIRecoveryDiagnosticProcessor | None = None


def lambda_handler(event, context):
    global _processor
    if _processor is None:
        _processor = build_processor()
    failures: list[dict[str, str]] = []
    request_id = str(getattr(context, "aws_request_id", "lambda-request"))
    for record in event.get("Records") or []:
        message_id = str(record.get("messageId") or "")
        try:
            message = AIRecoveryQueueMessage.model_validate_json(record.get("body") or "")
            _processor.process(
                bucket=message.bucket,
                ready_key=message.ready_key,
                expected_idempotency_key=message.idempotency_key,
            )
        except Exception as exc:
            failures.append({"itemIdentifier": message_id})
            logger.warning(
                "ai_recovery_message_failed request_id=%s message_id=%s error_type=%s error_code=%s",
                request_id,
                message_id,
                type(exc).__name__,
                _safe_error_code(exc),
            )
    return {"batchItemFailures": failures}


def build_processor() -> AIRecoveryDiagnosticProcessor:
    config = AIRecoveryConfig.from_mapping(os.environ)
    if config.mode.value == "off":
        raise RuntimeError("AI_RECOVERY_MODE must be shadow or active for the diagnostic Lambda.")
    bucket = _required_env("AI_RECOVERY_S3_BUCKET")
    region = config.region_name
    reader = S3AIRecoveryObjectReader(region=region)
    store = S3AIRecoveryResultStore(
        bucket=bucket,
        restricted_prefix=os.getenv("AI_RECOVERY_RESULT_S3_PREFIX", "ai-recovery/results/restricted/v1"),
        fixture_prefix=os.getenv("AI_RECOVERY_FIXTURE_S3_PREFIX", "ai-recovery/fixtures/v1"),
        region=region,
    )
    return AIRecoveryDiagnosticProcessor(
        object_reader=reader,
        analyzer=build_bedrock_nova_diagnostic_analyzer(config),
        result_store=store,
        fixture_builder=CanonicalV3PrivacyFixtureBuilder(max_pages=config.max_pages),
    )


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required by the AI recovery Lambda.")
    return value


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, AIExtractionError):
        return exc.code.value
    response = getattr(exc, "response", None)
    error = response.get("Error") if isinstance(response, dict) else None
    code = error.get("Code") if isinstance(error, dict) else None
    normalized = str(code or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", normalized):
        return normalized
    return "unclassified"
