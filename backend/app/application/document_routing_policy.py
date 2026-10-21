from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.application.document_type_classifier import (
    BANK_RECEIPT,
    FISCAL_INVOICE,
    INVESTMENT_SUMMARY,
    PAYMENT_REPORT,
    DocumentTypeClassification,
)
from app.application.errors import UnsupportedDocumentContentError
from app.application.unsupported_document_detection import (
    INVESTMENT_SUMMARY_USER_MESSAGE,
    PAYMENT_REPORT_USER_MESSAGE,
)

FISCAL_INVOICE_REJECTION_MIN_CONFIDENCE = 0.90
PAYMENT_REPORT_REJECTION_MIN_CONFIDENCE = 0.90
INVESTMENT_SUMMARY_REJECTION_MIN_CONFIDENCE = 0.90

FISCAL_INVOICE_REJECTION_MESSAGE = (
    "Este arquivo é uma nota fiscal eletrônica (NF-e ou NFS-e), não um extrato bancário. "
    "A conciliação de notas fiscais ainda não está disponível."
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
    if classification.document_type == BANK_RECEIPT and "receipt_payment_title" in classification.evidence:
        return DocumentRoutingDecision(DocumentRoutingAction.REJECT, "unsupported_payment_receipt")
    if classification.document_type == FISCAL_INVOICE and classification.confidence >= FISCAL_INVOICE_REJECTION_MIN_CONFIDENCE:
        return DocumentRoutingDecision(
            action=DocumentRoutingAction.REJECT,
            reason="high_confidence_fiscal_invoice",
        )
    if classification.document_type == PAYMENT_REPORT and classification.confidence >= PAYMENT_REPORT_REJECTION_MIN_CONFIDENCE:
        return DocumentRoutingDecision(
            action=DocumentRoutingAction.REJECT,
            reason="high_confidence_payment_report",
        )
    if classification.document_type == INVESTMENT_SUMMARY and classification.confidence >= INVESTMENT_SUMMARY_REJECTION_MIN_CONFIDENCE:
        return DocumentRoutingDecision(
            action=DocumentRoutingAction.REJECT,
            reason="high_confidence_investment_summary",
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
        message = {
            BANK_RECEIPT: ("Este arquivo é um comprovante de pagamento de títulos. "
                           "Comprovantes de pagamento ainda não são suportados; envie o extrato bancário com os lançamentos da conta."),
            PAYMENT_REPORT: PAYMENT_REPORT_USER_MESSAGE,
            INVESTMENT_SUMMARY: INVESTMENT_SUMMARY_USER_MESSAGE,
            FISCAL_INVOICE: FISCAL_INVOICE_REJECTION_MESSAGE,
        }[classification.document_type]
        error = UnsupportedDocumentContentError(
            document_type=classification.document_type,
            message=message,
        )
        setattr(error, "_document_type_classification", classification)
        raise error
    return decision


def enforce_initial_document_routing(
    classification: DocumentTypeClassification, *, filename: str, raw_bytes: bytes,
) -> DocumentRoutingDecision:
    if classification.document_type in {FISCAL_INVOICE, BANK_RECEIPT} and filename.lower().endswith(".pdf"):
        # A native annex in a compound PDF must not hide its statement.
        from app.application.pdf_document_segmentation import segment_pdf_pages

        try:
            reader = PdfReader(BytesIO(raw_bytes))
            pages = [page.extract_text() or "" for page in reader.pages]
        except (PdfReadError, ValueError, TypeError, KeyError):
            pages = []
        if pages and any(part["included_in_statement"] for part in segment_pdf_pages(pages).parts):
            return DocumentRoutingDecision(DocumentRoutingAction.CONTINUE, "compound_pdf_contains_statement")
    return enforce_document_routing(classification)
