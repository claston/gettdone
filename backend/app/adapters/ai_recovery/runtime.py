from __future__ import annotations

import os
from typing import Mapping

from app.adapters.ai_recovery.s3_request_publisher import S3AIRecoveryRequestPublisher
from app.adapters.ai_recovery.sqs_queue import SqsAIRecoveryQueuePublisher
from app.application.ai_recovery.config import AIRecoveryConfig, AIRecoveryMode
from app.application.ai_recovery.shadow_dispatch import AIRecoveryShadowDispatcher


def build_ai_recovery_shadow_dispatcher(
    values: Mapping[str, str] | None = None,
) -> AIRecoveryShadowDispatcher | None:
    environment = os.environ if values is None else values
    config = AIRecoveryConfig.from_mapping(environment)
    if config.mode == AIRecoveryMode.OFF:
        return None
    bucket = str(environment.get("AI_RECOVERY_S3_BUCKET") or "").strip()
    queue_url = str(environment.get("AI_RECOVERY_SQS_QUEUE_URL") or "").strip()
    if not bucket:
        raise RuntimeError("AI_RECOVERY_S3_BUCKET is required when AI recovery capture is enabled.")
    if config.bedrock_invocation_enabled and not queue_url:
        raise RuntimeError("AI_RECOVERY_SQS_QUEUE_URL is required when Bedrock invocation is enabled.")
    prefix = str(environment.get("AI_RECOVERY_REQUEST_S3_PREFIX") or "ai-recovery/requests/v1").strip()
    return AIRecoveryShadowDispatcher(
        config=config,
        bucket=bucket,
        request_publisher=S3AIRecoveryRequestPublisher(
            bucket=bucket,
            prefix=prefix,
            region=config.region_name,
        ),
        queue_publisher=(
            SqsAIRecoveryQueuePublisher(
                queue_url=queue_url,
                region=config.region_name,
            )
            if config.bedrock_invocation_enabled
            else None
        ),
        parser_release=str(environment.get("APP_RELEASE") or "local"),
        request_prefix=prefix,
        request_ttl_seconds=int(environment.get("AI_RECOVERY_REQUEST_TTL_SECONDS") or "86400"),
    )
