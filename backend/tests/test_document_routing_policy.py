from __future__ import annotations

import pytest

from app.application.document_routing_policy import (
    FISCAL_INVOICE_REJECTION_MIN_CONFIDENCE,
    DocumentRoutingAction,
    decide_document_routing,
    enforce_document_routing,
)
from app.application.document_type_classifier import (
    BANK_RECEIPT,
    CREDIT_CARD_STATEMENT,
    FISCAL_INVOICE,
    UNKNOWN_DOCUMENT,
    DocumentTypeClassification,
)
from app.application.errors import UnsupportedDocumentContentError


def _classification(document_type: str, confidence: float) -> DocumentTypeClassification:
    return DocumentTypeClassification(
        document_type=document_type,
        confidence=confidence,
        evidence=["test_signal"],
    )


def test_rejects_only_high_confidence_fiscal_invoice() -> None:
    classification = _classification(
        FISCAL_INVOICE,
        FISCAL_INVOICE_REJECTION_MIN_CONFIDENCE,
    )

    decision = decide_document_routing(classification)

    assert decision.action == DocumentRoutingAction.REJECT
    assert decision.reason == "high_confidence_fiscal_invoice"

    with pytest.raises(UnsupportedDocumentContentError) as exc_info:
        enforce_document_routing(classification)

    assert exc_info.value.document_type == FISCAL_INVOICE
    assert exc_info.value._document_type_classification == classification
    assert "nota fiscal eletrônica" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    "classification",
    [
        _classification(FISCAL_INVOICE, FISCAL_INVOICE_REJECTION_MIN_CONFIDENCE - 0.01),
        _classification(CREDIT_CARD_STATEMENT, 0.98),
        _classification(BANK_RECEIPT, 0.98),
        _classification(UNKNOWN_DOCUMENT, 0.0),
    ],
)
def test_continues_documents_outside_the_initial_rejection_policy(
    classification: DocumentTypeClassification,
) -> None:
    decision = enforce_document_routing(classification)

    assert decision.action == DocumentRoutingAction.CONTINUE
    assert decision.reason == "document_type_allowed"
