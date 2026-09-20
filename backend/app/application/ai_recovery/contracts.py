from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.application.ai_recovery.models import AIStatement


@dataclass(frozen=True, slots=True)
class AIExtractionUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class AIExtractionResult:
    statement: AIStatement
    model_id: str
    prompt_version: str
    provider_request_id: str | None = None
    usage: AIExtractionUsage | None = None


class DocumentAIExtractor(Protocol):
    """Synchronous boundary matching the current inline conversion runtime."""

    def extract(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        page_count: int,
    ) -> AIExtractionResult: ...
