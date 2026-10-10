from __future__ import annotations

import hashlib
import re
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
_CLASSIFICATION_EVIDENCE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:+-]{0,79}")
_CLASSIFICATION_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,79}")

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
    """Publish eligible conversions and content failures for isolated diagnosis."""

    def __init__(
        self,
        *,
        config: AIRecoveryConfig,
        bucket: str,
        request_publisher: AIRecoveryRequestPublisher,
        queue_publisher: AIRecoveryQueuePublisher | None,
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
        self.request_ttl_seconds = min(
            DEFAULT_REQUEST_TTL_SECONDS,
            max(300, int(request_ttl_seconds)),
        )
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
        is_generic = _is_generic_layout(layout_name)
        if is_generic and not issue_codes:
            issue_codes = ("generic_layout",)
        decision = assess_ai_recovery_eligibility(
            config=self.config,
            context=AIRecoveryEligibilityContext(
                file_type=document.file_type,
                page_count=page_count,
                case=(
                    AIRecoveryEligibilityCase.GENERIC_LAYOUT_CONVERSION
                    if is_generic
                    else AIRecoveryEligibilityCase.RECOGNIZED_LAYOUT_DIVERGENCE
                ),
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

        return self._publish(
            document=document,
            analysis_id=str(analysis.analysis_id),
            page_count=int(page_count),
            deterministic_artifact=_deterministic_artifact(analysis, selected_parser=selected_parser),
            source_evidence=_source_evidence(page_texts, source_layout_lines),
            layout_profile=layout_name,
            layout_family=layout_family,
            statement_type=statement_type,
            layout_confidence=_confidence(getattr(analysis, "layout_inference_confidence", None)),
            issue_codes=issue_codes,
        )

    def dispatch_failure(
        self,
        *,
        document: UploadedDocument,
        analysis_id: str,
        page_count: int | None,
        error_stage: str | None,
        error_subcode: str | None,
        exception_class: str,
        parse_observability: dict[str, object] | None = None,
        document_classification=None,
        document_processing_decision: str | None = None,
    ) -> AIRecoveryDispatchResult:
        observability = dict(parse_observability or {})
        decision = assess_ai_recovery_eligibility(
            config=self.config,
            context=AIRecoveryEligibilityContext(
                file_type=document.file_type,
                page_count=page_count,
                case=AIRecoveryEligibilityCase.CONTENT_EXTRACTION_FAILURE,
                error_stage=error_stage,
                error_subcode=error_subcode,
            ),
        )
        if not decision.eligible:
            return AIRecoveryDispatchResult(status="skipped", reason=decision.reason.value)

        layout_name = str(observability.get("layout_inference_name") or "unknown").strip() or "unknown"
        layout_family, statement_type = _layout_metadata(layout_name)
        selected_parser = str(observability.get("selected_parser") or "").strip()
        normalized_subcode = str(error_subcode or "content_extraction_failure").strip().lower()
        classification_metadata = (
            _document_classification_metadata(
                document_classification,
                processing_decision=document_processing_decision,
            )
            if normalized_subcode == "unsupported_document_type"
            else None
        )
        deterministic_artifact = {
            "schema_version": "deterministic_statement_v1",
            "analysis_id": analysis_id,
            "selected_parser": selected_parser,
            "parser_metrics": {},
            "opening_balance": None,
            "closing_balance": None,
            "transactions": [],
            "failure": {
                "error_stage": str(error_stage or ""),
                "error_subcode": normalized_subcode,
                "exception_class": str(exception_class or "Exception"),
            },
        }
        if classification_metadata is not None:
            deterministic_artifact["document_classification"] = classification_metadata
        return self._publish(
            document=document,
            analysis_id=analysis_id,
            page_count=int(page_count),
            deterministic_artifact=deterministic_artifact,
            source_evidence=_source_evidence(None, None),
            layout_profile=layout_name,
            layout_family=layout_family or "unknown",
            statement_type=statement_type or "unknown",
            layout_confidence=_confidence(observability.get("layout_inference_confidence")),
            issue_codes=(normalized_subcode,),
            document_type=(classification_metadata or {}).get("document_type"),
            document_type_confidence=(classification_metadata or {}).get("confidence"),
            document_classification_version=(classification_metadata or {}).get("classifier_version"),
            document_processing_decision=(classification_metadata or {}).get("processing_decision"),
            document_classification_evidence=tuple(
                (classification_metadata or {}).get("evidence") or []
            ),
            enqueue=normalized_subcode != "unsupported_document_type",
        )

    def _publish(
        self,
        *,
        document: UploadedDocument,
        analysis_id: str,
        page_count: int,
        deterministic_artifact: dict[str, object],
        source_evidence: dict[str, object],
        layout_profile: str,
        layout_family: str,
        statement_type: str,
        layout_confidence: float,
        issue_codes: tuple[str, ...],
        document_type: object = None,
        document_type_confidence: object = None,
        document_classification_version: object = None,
        document_processing_decision: object = None,
        document_classification_evidence: tuple[str, ...] = (),
        enqueue: bool = True,
    ) -> AIRecoveryDispatchResult:
        created_at = self.clock()
        versions = AIRecoveryVersionSet(
            model_id=self.config.model_id,
            prompt_version=PROMPT_VERSION,
            output_schema_version=OUTPUT_SCHEMA_VERSION,
            comparator_version=COMPARATOR_VERSION,
        )
        artifacts = build_ai_recovery_request_artifacts(
            bucket=self.bucket,
            analysis_id=analysis_id,
            pdf_bytes=document.raw_bytes,
            page_count=page_count,
            deterministic_artifact=deterministic_artifact,
            source_evidence=source_evidence,
            parser_release=self.parser_release,
            layout_profile=layout_profile,
            layout_family=layout_family,
            statement_type=statement_type,
            layout_confidence=layout_confidence,
            issue_codes=issue_codes,
            ai=versions,
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=self.request_ttl_seconds),
            document_type=str(document_type or "").strip() or None,
            document_type_confidence=(
                _confidence(document_type_confidence)
                if document_type_confidence is not None
                else None
            ),
            document_classification_version=(
                str(document_classification_version or "").strip() or None
            ),
            document_processing_decision=(
                str(document_processing_decision or "").strip() or None
            ),
            document_classification_evidence=(
                document_classification_evidence or None
            ),
            prefix=self.request_prefix,
        )
        publication = self.request_publisher.publish(artifacts)
        if not enqueue:
            return AIRecoveryDispatchResult(
                status="stored",
                reason="manual_classification_review",
                idempotency_key=artifacts.manifest.idempotency_key,
                ready_key=publication.ready_key,
            )
        if not self.config.bedrock_invocation_enabled:
            return AIRecoveryDispatchResult(
                status="stored",
                reason="bedrock_disabled",
                idempotency_key=artifacts.manifest.idempotency_key,
                ready_key=publication.ready_key,
            )
        if self.queue_publisher is None:
            raise RuntimeError("AI recovery queue publisher is required when Bedrock invocation is enabled.")
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


def _document_classification_metadata(
    classification,
    *,
    processing_decision: str | None,
) -> dict[str, object]:
    document_type = str(getattr(classification, "document_type", None) or "").strip().lower()
    if not _CLASSIFICATION_IDENTIFIER_PATTERN.fullmatch(document_type):
        document_type = "unknown"
    classifier_version = str(getattr(classification, "classifier_version", None) or "").strip()
    if not _CLASSIFICATION_IDENTIFIER_PATTERN.fullmatch(classifier_version):
        classifier_version = "unknown"
    normalized_decision = str(processing_decision or "").strip().lower()
    if normalized_decision not in {"rejected", "failed", "accepted", "processing"}:
        normalized_decision = "rejected"
    evidence = tuple(
        dict.fromkeys(
            value
            for raw_value in (getattr(classification, "evidence", None) or [])
            if (value := str(raw_value or "").strip().lower())
            and _CLASSIFICATION_EVIDENCE_PATTERN.fullmatch(value)
        )
    )[:20]
    return {
        "document_type": document_type,
        "confidence": _confidence(getattr(classification, "confidence", None)),
        "evidence": list(evidence),
        "classifier_version": classifier_version,
        "processing_decision": normalized_decision,
    }


def _layout_metadata(layout_name: str) -> tuple[str, str]:
    if _is_generic_layout(layout_name):
        return "generic_statement", "generic_statement"
    profile = get_layout_profile(layout_name)
    if profile is not None:
        return profile.layout_family or profile.profile_name, profile.statement_type
    return _LEGACY_LAYOUT_METADATA.get(layout_name, ("", ""))


def _is_generic_layout(layout_name: str) -> bool:
    return str(layout_name or "").strip().lower() in {
        "generic",
        "generic_pdf",
        "generic_statement_ptbr",
        "unknown",
    }


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


def _confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, confidence))


def _money(value: object) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def _optional_money(value: object) -> str | None:
    return None if value is None else _money(value)
