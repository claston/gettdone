from __future__ import annotations

import json
import re

DOCUMENT_TYPES = frozenset(
    {
        "bank_statement",
        "fiscal_invoice",
        "billing_report",
        "bank_receipt",
        "credit_card_statement",
        "financial_spreadsheet",
        "other",
        "unknown",
    }
)
DOCUMENT_PROCESSING_DECISIONS = frozenset({"processing", "accepted", "failed", "rejected"})
_EVIDENCE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:+-]{0,79}")


def prepare_document_classification_record(
    *,
    document_type: str | None,
    document_type_confidence: float | None,
    document_classification_version: str | None,
    document_processing_decision: str | None,
    document_classification_evidence: list[str] | None,
) -> tuple[str | None, float | None, str | None, str | None, str | None]:
    normalized_type = str(document_type or "").strip().lower()
    if normalized_type not in DOCUMENT_TYPES:
        normalized_type = ""

    confidence = None
    if document_type_confidence is not None:
        confidence = max(0.0, min(1.0, float(document_type_confidence)))

    version = str(document_classification_version or "").strip()[:64] or None
    decision = str(document_processing_decision or "").strip().lower()
    if decision not in DOCUMENT_PROCESSING_DECISIONS:
        decision = ""

    evidence_ids = sorted(
        {
            value
            for raw_value in (document_classification_evidence or [])
            if (value := str(raw_value or "").strip().lower())
            and _EVIDENCE_ID_PATTERN.fullmatch(value)
        }
    )[:20]
    evidence_json = json.dumps(evidence_ids, ensure_ascii=True) if evidence_ids else None
    return normalized_type or None, confidence, version, decision or None, evidence_json
