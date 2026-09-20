from __future__ import annotations

import json
import logging
from importlib import resources
from pathlib import Path
from time import perf_counter

from pydantic import ValidationError

from app.application.ai_recovery.config import AIRecoveryConfig
from app.application.ai_recovery.contracts import AIExtractionResult, AIExtractionUsage
from app.application.ai_recovery.errors import AIExtractionError, AIExtractionErrorCode
from app.application.ai_recovery.models import AIStatement

logger = logging.getLogger(__name__)

PROMPT_VERSION = "nova_bank_statement_v1"
MAX_DIRECT_PDF_BYTES = 25 * 1024 * 1024
_USER_INSTRUCTION = "Transcribe the attached bank statement using the required JSON schema."


class BedrockNovaDocumentAIExtractor:
    def __init__(
        self,
        *,
        client,
        model_id: str,
        timeout_seconds: int,
        max_pages: int,
        max_output_tokens: int,
        prompt: str | None = None,
    ) -> None:
        self.client = client
        self.model_id = str(model_id or "").strip()
        self.timeout_seconds = int(timeout_seconds)
        self.max_pages = int(max_pages)
        self.max_output_tokens = int(max_output_tokens)
        self.prompt = prompt if prompt is not None else _load_prompt()
        if not self.model_id or not self.prompt.strip():
            raise ValueError("Bedrock AI extraction requires a model id and prompt.")
        if not 1 <= self.timeout_seconds <= 30:
            raise ValueError("Bedrock AI extraction timeout must be between 1 and 30 seconds.")
        if not 1 <= self.max_pages <= 15:
            raise ValueError("Bedrock AI extraction page limit must be between 1 and 15.")
        if not 256 <= self.max_output_tokens <= 64000:
            raise ValueError("Bedrock AI extraction output limit must be between 256 and 64000 tokens.")

    def extract(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        page_count: int,
    ) -> AIExtractionResult:
        self._validate_document(filename=filename, raw_bytes=raw_bytes, page_count=page_count)
        started_at = perf_counter()
        request = {
            "modelId": self.model_id,
            "system": [{"text": self.prompt}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"text": _USER_INSTRUCTION},
                        {
                            "document": {
                                "format": "pdf",
                                "name": "bank-statement",
                                "source": {"bytes": raw_bytes},
                            }
                        },
                    ],
                }
            ],
            "inferenceConfig": {"maxTokens": self.max_output_tokens, "temperature": 0},
        }
        try:
            response = self.client.converse(**request)
        except Exception as exc:
            error = _map_provider_error(exc)
            logger.warning(
                "ai_recovery_provider_failed model_id=%s prompt_version=%s error_code=%s",
                self.model_id,
                PROMPT_VERSION,
                error.code.value,
            )
            raise error from None

        latency_ms = round((perf_counter() - started_at) * 1000, 3)
        statement = _parse_statement_response(response)
        usage = _parse_usage(response)
        request_id = _parse_request_id(response)
        logger.info(
            (
                "ai_recovery_provider_completed model_id=%s prompt_version=%s latency_ms=%.3f "
                "input_tokens=%s output_tokens=%s"
            ),
            self.model_id,
            PROMPT_VERSION,
            latency_ms,
            usage.input_tokens if usage is not None else 0,
            usage.output_tokens if usage is not None else 0,
        )
        return AIExtractionResult(
            statement=statement,
            model_id=self.model_id,
            prompt_version=PROMPT_VERSION,
            provider_request_id=request_id,
            usage=usage,
            latency_ms=latency_ms,
        )

    def _validate_document(self, *, filename: str, raw_bytes: bytes, page_count: int) -> None:
        if Path(filename or "").suffix.lower() != ".pdf":
            raise AIExtractionError(AIExtractionErrorCode.INVALID_DOCUMENT)
        if not isinstance(raw_bytes, bytes) or not raw_bytes.startswith(b"%PDF"):
            raise AIExtractionError(AIExtractionErrorCode.INVALID_DOCUMENT)
        if not raw_bytes or len(raw_bytes) > MAX_DIRECT_PDF_BYTES:
            raise AIExtractionError(AIExtractionErrorCode.INVALID_DOCUMENT)
        if isinstance(page_count, bool) or not isinstance(page_count, int) or not 1 <= page_count <= self.max_pages:
            raise AIExtractionError(AIExtractionErrorCode.INVALID_DOCUMENT)


def build_bedrock_nova_extractor(config: AIRecoveryConfig) -> BedrockNovaDocumentAIExtractor:
    try:
        import boto3
        from botocore.config import Config
    except Exception as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("Bedrock dependencies are not installed.") from exc

    sdk_config = Config(
        connect_timeout=min(5, config.timeout_seconds),
        read_timeout=config.timeout_seconds,
        retries={"total_max_attempts": 1, "mode": "standard"},
    )
    client = boto3.client("bedrock-runtime", region_name=config.region_name, config=sdk_config)
    return BedrockNovaDocumentAIExtractor(
        client=client,
        model_id=config.model_id,
        timeout_seconds=config.timeout_seconds,
        max_pages=config.max_pages,
        max_output_tokens=config.max_output_tokens,
    )


def _load_prompt() -> str:
    prompt_file = resources.files("app.application.ai_recovery.prompts").joinpath(f"{PROMPT_VERSION}.txt")
    return prompt_file.read_text(encoding="utf-8").strip()


def _parse_statement_response(response: object) -> AIStatement:
    if not isinstance(response, dict):
        raise AIExtractionError(AIExtractionErrorCode.INVALID_RESPONSE_JSON)
    stop_reason = str(response.get("stopReason") or "").strip().lower()
    if stop_reason != "end_turn":
        raise AIExtractionError(AIExtractionErrorCode.INCOMPLETE_RESPONSE)

    output = response.get("output")
    message = output.get("message") if isinstance(output, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    text_blocks = [item.get("text") for item in content or [] if isinstance(item, dict) and isinstance(item.get("text"), str)]
    raw_text = "".join(text_blocks).strip()
    if not raw_text:
        raise AIExtractionError(AIExtractionErrorCode.INVALID_RESPONSE_JSON)
    try:
        payload = json.loads(raw_text)
    except (TypeError, ValueError):
        raise AIExtractionError(AIExtractionErrorCode.INVALID_RESPONSE_JSON) from None
    try:
        return AIStatement.model_validate(payload)
    except ValidationError:
        raise AIExtractionError(AIExtractionErrorCode.INVALID_RESPONSE_SCHEMA) from None


def _parse_usage(response: dict[str, object]) -> AIExtractionUsage | None:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    try:
        input_tokens = max(0, int(usage.get("inputTokens") or 0))
        output_tokens = max(0, int(usage.get("outputTokens") or 0))
    except (TypeError, ValueError):
        return None
    return AIExtractionUsage(input_tokens=input_tokens, output_tokens=output_tokens)


def _parse_request_id(response: dict[str, object]) -> str | None:
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return None
    request_id = str(metadata.get("RequestId") or "").strip()
    return request_id or None


def _map_provider_error(exc: Exception) -> AIExtractionError:
    if type(exc).__name__ in {
        "ConnectTimeoutError",
        "ReadTimeoutError",
    }:
        return AIExtractionError(AIExtractionErrorCode.PROVIDER_TIMEOUT)
    if type(exc).__name__ in {
        "ClientConnectionError",
        "ConnectionClosedError",
        "EndpointConnectionError",
    }:
        return AIExtractionError(AIExtractionErrorCode.PROVIDER_UNAVAILABLE)

    response = getattr(exc, "response", None)
    error = response.get("Error") if isinstance(response, dict) and isinstance(response.get("Error"), dict) else {}
    code = str(error.get("Code") or "")
    if code in {"AccessDeniedException", "ValidationException", "ResourceNotFoundException"}:
        return AIExtractionError(AIExtractionErrorCode.PROVIDER_REJECTED)
    if code in {
        "InternalServerException",
        "ModelNotReadyException",
        "ServiceUnavailableException",
        "ThrottlingException",
    }:
        return AIExtractionError(AIExtractionErrorCode.PROVIDER_UNAVAILABLE)
    if code == "ModelTimeoutException":
        return AIExtractionError(AIExtractionErrorCode.PROVIDER_TIMEOUT)
    return AIExtractionError(AIExtractionErrorCode.PROVIDER_UNAVAILABLE)
