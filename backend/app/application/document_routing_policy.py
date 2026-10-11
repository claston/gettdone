from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.application.document_type_classifier import (
    FISCAL_INVOICE,
    DocumentTypeClassification,
)
from app.application.errors import UnsupportedDocumentContentError

FISCAL_INVOICE_REJECTION_MIN_CONFIDENCE = 0.90

FISCAL_INVOICE_REJECTION_MESSAGE = (
    "Este arquivo é uma nota fiscal eletrônica (NF-e), não um extrato bancário. A conciliação de notas fiscais ainda não está disponível."
)


class DocumentRoutingAction(str, Enum):
    CONTINUE = "continue"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class DocumentRoutingDecision:
    action: DocumentRoutingAction
    reason: str


def decide_document_routing(
    classification: DocumentTypeClassification,
) -> DocumentRoutingDecision:
    if classification.document_type == FISCAL_INVOICE and classification.confidence >= FISCAL_INVOICE_REJECTION_MIN_CONFIDENCE:
        return DocumentRoutingDecision(
            action=DocumentRoutingAction.REJECT,
            reason="high_confidence_fiscal_invoice",
        )
    return DocumentRoutingDecision(
        action=DocumentRoutingAction.CONTINUE,
        reason="document_type_allowed",
    )


def enforce_document_routing(
    classification: DocumentTypeClassification,
) -> DocumentRoutingDecision:
    decision = decide_document_routing(classification)
    if decision.action == DocumentRoutingAction.REJECT:
        error = UnsupportedDocumentContentError(
            document_type=classification.document_type,
            message=FISCAL_INVOICE_REJECTION_MESSAGE,
        )
        setattr(error, "_document_type_classification", classification)
        raise error
    return decision
