from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Callable

from app.application.ai_recovery.comparator import COMPARATOR_VERSION
from app.application.ai_recovery.config import AIRecoveryConfig
from app.application.ai_recovery.contracts import AIRecoveryRequestPublisher
from app.application.ai_recovery.eligibility import (
    AIRecoveryEligibilityCase,
    AIRecoveryEligibilityContext,
    assess_ai_recovery_eligibility,
)
from app.application.ai_recovery.queue import AIRecoveryQueueMessage, AIRecoveryQueuePublisher
from app.application.ai_recovery.request_publishing import build_ai_recovery_request_artifacts
from app.application.ai_recovery.schemas import AIRecoveryVersionSet
from app.application.conversion.uploaded_document import UploadedDocument
from app.application.layout_profiles.registry import get_layout_profile

PROMPT_VERSION = "nova_transaction_diagnosis_v1"
OUTPUT_SCHEMA_VERSION = "nova_diagnostic_v1"
DEFAULT_REQUEST_TTL_SECONDS = 86_400

_LEGACY_LAYOUT_METADATA = {
    "nubank_statement_ptbr": ("nubank_conta_digital", "conta_digital_extrato"),
    "bradesco_statement_ptbr": ("bradesco_conta_corrente", "conta_corrente_extrato"),
    "itau_statement_ptbr": ("itau_conta_corrente", "conta_corrente_extrato"),
    "santander_statement_ptbr": ("santander_conta_corrente", "conta_corrente_extrato"),
}


@dataclass(frozen=True, slots=True)
class AIRecoveryDispatchResult:
    status: str
    reason: str
    idempotency_key: str | None = None
    ready_key: str | None = None
    message_id: str | None = None


class AIRecoveryShadowDispatcher:
    """Publish eligible successful conversions for isolated, asynchronous diagnosis."""

    def __init__(
        self,
        *,
        config: AIRecoveryConfig,
        bucket: str,
        request_publisher: AIRecoveryRequestPublisher,
        queue_publisher: AIRecoveryQueuePublisher,
        parser_release: str,
        request_prefix: str = "ai-recovery/requests/v1",
        request_ttl_seconds: int = DEFAULT_REQUEST_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.bucket = str(bucket or "").strip()
        self.request_publisher = request_publisher
        self.queue_publisher = queue_publisher
        self.parser_release = str(parser_release or "").strip() or "unknown"
        self.request_prefix = request_prefix
        self.request_ttl_seconds = max(300, int(request_ttl_seconds))
        self.clock = clock or (lambda: datetime.now(UTC))

    def dispatch(
        self,
        *,
        document: UploadedDocument,
        analysis,
        page_texts: tuple[str, ...] | None,
        source_layout_lines,
    ) -> AIRecoveryDispatchResult:
        layout_name = str(getattr(analysis, "layout_inference_name", None) or "").strip()
        layout_family, statement_type = _layout_metadata(layout_name)
        metrics = getattr(analysis, "pdf_processing_metrics", None)
        selected_parser = _metric(metrics, "selected_parser")
        page_count = _optional_int(_metric(metrics, "page_count"))
        issue_codes = _issue_codes(analysis)
        decision = assess_ai_recovery_eligibility(
            config=self.config,
            context=AIRecoveryEligibilityContext(
                file_type=document.file_type,
                page_count=page_count,
                case=AIRecoveryEligibilityCase.RECOGNIZED_LAYOUT_DIVERGENCE,
                file_size_bytes=document.size_bytes,
                document_sha256=hashlib.sha256(document.raw_bytes).hexdigest(),
                transaction_count=int(getattr(analysis, "transactions_total", 0) or 0),
                layout_name=layout_name,
                layout_confidence=getattr(analysis, "layout_inference_confidence", None),
                statement_type=statement_type,
                selected_parser=str(selected_parser or "").strip() or None,
                issue_codes=issue_codes,
                source_evidence_available=bool(page_texts),
            ),
        )
        if not decision.eligible:
            return AIRecoveryDispatchResult(status="skipped", reason=decision.reason.value)

        created_at = self.clock()
        versions = AIRecoveryVersionSet(
            model_id=self.config.model_id,
            prompt_version=PROMPT_VERSION,
            output_schema_version=OUTPUT_SCHEMA_VERSION,
            comparator_version=COMPARATOR_VERSION,
        )
        artifacts = build_ai_recovery_request_artifacts(
            bucket=self.bucket,
            analysis_id=str(analysis.analysis_id),
            pdf_bytes=document.raw_bytes,
            page_count=int(page_count),
            deterministic_artifact=_deterministic_artifact(analysis, selected_parser=selected_parser),
            source_evidence=_source_evidence(page_texts, source_layout_lines),
            parser_release=self.parser_release,
            layout_profile=layout_name,
            layout_family=layout_family,
            statement_type=statement_type,
            layout_confidence=float(analysis.layout_inference_confidence),
            issue_codes=issue_codes,
            ai=versions,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=self.request_ttl_seconds),
            prefix=self.request_prefix,
        )
        publication = self.request_publisher.publish(artifacts)
        message = AIRecoveryQueueMessage(
            idempotency_key=artifacts.manifest.idempotency_key,
            bucket=self.bucket,
            ready_key=publication.ready_key,
        )
        message_id = self.queue_publisher.publish(message)
        return AIRecoveryDispatchResult(
            status="queued",
            reason="eligible",
            idempotency_key=artifacts.manifest.idempotency_key,
            ready_key=publication.ready_key,
            message_id=message_id,
        )


def _layout_metadata(layout_name: str) -> tuple[str, str]:
    profile = get_layout_profile(layout_name)
    if profile is not None:
        return profile.layout_family or profile.profile_name, profile.statement_type
    return _LEGACY_LAYOUT_METADATA.get(layout_name, ("", ""))


def _issue_codes(analysis) -> tuple[str, ...]:
    codes = [
        str(item.get("issue_code") or "").strip().lower()
        for item in (getattr(analysis, "quality_issues", None) or [])
        if isinstance(item, dict)
    ]
    metrics = getattr(analysis, "pdf_processing_metrics", None)
    warning_types = _metric(metrics, "canonical_warning_types_list")
    if isinstance(warning_types, (list, tuple)):
        codes.extend(str(item or "").strip().lower() for item in warning_types)
    if _optional_int(_metric(metrics, "balance_consistency_failed")):
        codes.append("balance_consistency_failed")
    return tuple(dict.fromkeys(code for code in codes if code))


def _deterministic_artifact(analysis, *, selected_parser: object) -> dict[str, object]:
    issues_by_index: dict[int, list[dict[str, object]]] = {}
    for issue in getattr(analysis, "quality_issues", None) or []:
        if not isinstance(issue, dict):
            continue
        index = _optional_int(issue.get("transaction_index"))
        if index is not None:
            issues_by_index.setdefault(index, []).append(dict(issue))
    rows = getattr(analysis, "report_transactions", None) or getattr(analysis, "preview_transactions", None) or []
    transactions = []
    for index, row in enumerate(rows):
        transactions.append(
            {
                "index": index,
                "date": str(getattr(row, "date", "") or ""),
                "description": str(getattr(row, "description", "") or ""),
                "amount": _money(getattr(row, "amount", 0)),
                "running_balance": _optional_money(getattr(row, "running_balance", None)),
                "warning_types": list(getattr(row, "warning_types", None) or []),
                "source_issues": issues_by_index.get(index, []),
            }
        )
    metrics = getattr(analysis, "pdf_processing_metrics", None)
    safe_metric_names = (
        "parser_selection_reason",
        "confidence_band",
        "canonical_warning_transactions_count",
        "balance_consistency_failed",
        "page_count",
        "extracted_char_count",
        "extraction_provider",
    )
    parser_metrics = {
        name: value
        for name in safe_metric_names
        if (value := _metric(metrics, name)) is not None and isinstance(value, (str, int, float, bool))
    }
    return {
        "schema_version": "deterministic_statement_v1",
        "analysis_id": str(analysis.analysis_id),
        "selected_parser": str(selected_parser or ""),
        "parser_metrics": parser_metrics,
        "opening_balance": _optional_money(getattr(analysis, "opening_balance", None)),
        "closing_balance": _optional_money(getattr(analysis, "closing_balance", None)),
        "transactions": transactions,
    }


def _source_evidence(page_texts, source_layout_lines) -> dict[str, object]:
    pages: list[dict[str, object]] = []
    texts = tuple(str(value or "") for value in (page_texts or ()))
    layouts = source_layout_lines or ()
    for page_index in range(max(len(texts), len(layouts))):
        lines = layouts[page_index] if page_index < len(layouts) else ()
        pages.append(
            {
                "page": page_index + 1,
                "text": texts[page_index] if page_index < len(texts) else "",
                "lines": [
                    {
                        "id": str(getattr(line, "id", "") or ""),
                        "line_index": int(getattr(line, "line_index", 0) or 0),
                        "text": str(getattr(line, "text", "") or ""),
                        "bbox": getattr(line, "bbox", None),
                    }
                    for line in lines
                ],
            }
        )
    return {"schema_version": "source_evidence_v1", "classification": "restricted", "pages": pages}


def _metric(metrics: object, name: str):
    if isinstance(metrics, dict):
        return metrics.get(name)
    return getattr(metrics, name, None) if metrics is not None else None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _money(value: object) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _optional_money(value: object) -> str | None:
    return None if value is None else _money(value)
