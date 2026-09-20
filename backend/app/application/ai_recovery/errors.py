from __future__ import annotations

from enum import Enum


class AIExtractionErrorCode(str, Enum):
    INVALID_DOCUMENT = "invalid_document"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_REJECTED = "provider_rejected"
    INCOMPLETE_RESPONSE = "incomplete_response"
    INVALID_RESPONSE_JSON = "invalid_response_json"
    INVALID_RESPONSE_SCHEMA = "invalid_response_schema"


_SAFE_MESSAGES = {
    AIExtractionErrorCode.INVALID_DOCUMENT: "The document is not eligible for AI extraction.",
    AIExtractionErrorCode.PROVIDER_TIMEOUT: "The AI extraction provider timed out.",
    AIExtractionErrorCode.PROVIDER_UNAVAILABLE: "The AI extraction provider is temporarily unavailable.",
    AIExtractionErrorCode.PROVIDER_REJECTED: "The AI extraction provider rejected the request.",
    AIExtractionErrorCode.INCOMPLETE_RESPONSE: "The AI extraction provider returned an incomplete response.",
    AIExtractionErrorCode.INVALID_RESPONSE_JSON: "The AI extraction provider returned invalid JSON.",
    AIExtractionErrorCode.INVALID_RESPONSE_SCHEMA: "The AI extraction provider returned an invalid statement schema.",
}


class AIExtractionError(Exception):
    def __init__(self, code: AIExtractionErrorCode, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or _SAFE_MESSAGES[code])
