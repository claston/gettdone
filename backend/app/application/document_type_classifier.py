from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.application.normalization.text import normalize_upper_text
from app.application.unsupported_document_detection import detect_unsupported_document_type

BANK_STATEMENT = "bank_statement"
FISCAL_INVOICE = "fiscal_invoice"
BILLING_REPORT = "billing_report"
BANK_RECEIPT = "bank_receipt"
CREDIT_CARD_STATEMENT = "credit_card_statement"
FINANCIAL_SPREADSHEET = "financial_spreadsheet"
OTHER_DOCUMENT = "other"
UNKNOWN_DOCUMENT = "unknown"

DOCUMENT_TYPE_CLASSIFIER_VERSION = "2026-10-10.1"

_PDF_PREVIEW_PAGE_LIMIT = 3
_PDF_PREVIEW_CHAR_LIMIT = 16_000
_NFE_ACCESS_KEY_PATTERN = re.compile(r"(?<!\d)(?:\d[ .-]*){44}(?!\d)")


@dataclass(frozen=True)
class DocumentTypeClassification:
    document_type: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    classifier_version: str = DOCUMENT_TYPE_CLASSIFIER_VERSION


def classify_document_type(
    filename: str,
    raw_bytes: bytes,
    *,
    extracted_text: str | None = None,
    layout_inference_name: str | None = None,
    layout_inference_confidence: float | None = None,
) -> DocumentTypeClassification:
    extension = Path(filename or "").suffix.lower().lstrip(".")
    text = str(extracted_text or "").strip() or _extract_text_preview(extension, raw_bytes)
    normalized_text = normalize_upper_text(text)

    if extension == "ofx" or "<OFX" in normalized_text:
        return _classification(BANK_STATEMENT, 1.0, "ofx_structure")

    unsupported_type = detect_unsupported_document_type(text)
    if unsupported_type == BILLING_REPORT:
        return _classification(BILLING_REPORT, 0.99, "billing_report_structure")

    invoice_evidence = _invoice_evidence(normalized_text, text)
    if _has_strong_invoice_identity(invoice_evidence):
        confidence = min(0.99, 0.72 + (0.07 * len(invoice_evidence)))
        return DocumentTypeClassification(
            document_type=FISCAL_INVOICE,
            confidence=round(confidence, 2),
            evidence=invoice_evidence,
        )

    statement_evidence = _bank_statement_evidence(
        normalized_text,
        layout_inference_name=layout_inference_name,
        layout_inference_confidence=layout_inference_confidence,
    )
    if _has_strong_statement_identity(statement_evidence):
        confidence = min(0.99, 0.7 + (0.06 * len(statement_evidence)))
        return DocumentTypeClassification(
            document_type=BANK_STATEMENT,
            confidence=round(confidence, 2),
            evidence=statement_evidence,
        )

    card_evidence = _credit_card_evidence(normalized_text)
    if _has_strong_credit_card_identity(card_evidence):
        return DocumentTypeClassification(
            document_type=CREDIT_CARD_STATEMENT,
            confidence=min(0.98, round(0.62 + (0.1 * len(card_evidence)), 2)),
            evidence=card_evidence,
        )

    receipt_evidence = _bank_receipt_evidence(normalized_text)
    if _has_strong_bank_receipt_identity(receipt_evidence):
        return DocumentTypeClassification(
            document_type=BANK_RECEIPT,
            confidence=min(0.98, round(0.62 + (0.1 * len(receipt_evidence)), 2)),
            evidence=receipt_evidence,
        )

    if extension in {"csv", "xlsx"}:
        return _classification(FINANCIAL_SPREADSHEET, 0.85, f"{extension}_tabular_file")

    return DocumentTypeClassification(
        document_type=UNKNOWN_DOCUMENT,
        confidence=0.0,
        evidence=[],
    )


def _classification(document_type: str, confidence: float, evidence: str) -> DocumentTypeClassification:
    return DocumentTypeClassification(
        document_type=document_type,
        confidence=confidence,
        evidence=[evidence],
    )


def _invoice_evidence(normalized_text: str, raw_text: str) -> list[str]:
    evidence: list[str] = []
    if _contains_token(normalized_text, "DANFE"):
        evidence.append("nfe_danfe")
    if "DOCUMENTO AUXILIAR DA NOTA FISCAL ELETRONICA" in normalized_text:
        evidence.append("nfe_auxiliary_document_title")
    if "CHAVE DE ACESSO" in normalized_text and _NFE_ACCESS_KEY_PATTERN.search(raw_text):
        evidence.append("nfe_access_key")
    if "PROTOCOLO DE AUTORIZACAO DE USO" in normalized_text:
        evidence.append("nfe_authorization_protocol")
    if "EMITENTE" in normalized_text and "DESTINATARIO" in normalized_text:
        evidence.append("nfe_parties")
    if "DADOS DOS PRODUTOS" in normalized_text and "SERVICOS" in normalized_text:
        evidence.append("nfe_products_services_table")
    return evidence


def _has_strong_invoice_identity(evidence: list[str]) -> bool:
    evidence_set = set(evidence)
    has_title = bool({"nfe_danfe", "nfe_auxiliary_document_title"} & evidence_set)
    has_fiscal_identity = bool({"nfe_access_key", "nfe_authorization_protocol"} & evidence_set)
    return len(evidence_set) >= 3 and (has_title or has_fiscal_identity)


def _credit_card_evidence(normalized_text: str) -> list[str]:
    evidence: list[str] = []
    if "FATURA" in normalized_text:
        evidence.append("card_invoice_title")
    if "CARTAO" in normalized_text:
        evidence.append("card_product")
    if "VENCIMENTO" in normalized_text:
        evidence.append("card_due_date")
    if "PAGAMENTO MINIMO" in normalized_text:
        evidence.append("card_minimum_payment")
    if "LIMITE DISPONIVEL" in normalized_text or "LIMITE DE CREDITO" in normalized_text:
        evidence.append("card_credit_limit")
    return evidence


def _has_strong_credit_card_identity(evidence: list[str]) -> bool:
    evidence_set = set(evidence)
    return {
        "card_invoice_title",
        "card_product",
    }.issubset(evidence_set) and len(evidence_set) >= 3


def _bank_receipt_evidence(normalized_text: str) -> list[str]:
    evidence: list[str] = []
    if "COMPROVANTE" in normalized_text:
        evidence.append("receipt_title")
    if any(token in normalized_text for token in ("TRANSFERENCIA", "PIX", "PAGAMENTO")):
        evidence.append("receipt_operation")
    if "VALOR" in normalized_text:
        evidence.append("receipt_amount")
    if "DATA DA TRANSACAO" in normalized_text or "DATA DO PAGAMENTO" in normalized_text:
        evidence.append("receipt_date")
    if "ID DA TRANSACAO" in normalized_text or "AUTENTICACAO" in normalized_text:
        evidence.append("receipt_authentication")
    return evidence


def _has_strong_bank_receipt_identity(evidence: list[str]) -> bool:
    evidence_set = set(evidence)
    return "receipt_title" in evidence_set and len(evidence_set) >= 3


def _bank_statement_evidence(
    normalized_text: str,
    *,
    layout_inference_name: str | None,
    layout_inference_confidence: float | None,
) -> list[str]:
    evidence: list[str] = []
    normalized_layout = normalize_upper_text(layout_inference_name or "")
    if normalized_layout and normalized_layout != "GENERIC STATEMENT PTBR" and (
        "STATEMENT" in normalized_layout or "EXTRATO" in normalized_layout
    ):
        evidence.append("statement_layout")
        if layout_inference_confidence is not None and layout_inference_confidence >= 0.85:
            evidence.append("statement_layout_high_confidence")
    if "EXTRATO" in normalized_text:
        evidence.append("statement_title")
    if "CONTA CORRENTE" in normalized_text or "CONTA DIGITAL" in normalized_text:
        evidence.append("statement_account")
    if "SALDO" in normalized_text:
        evidence.append("statement_balance")
    if "DATA" in normalized_text and "VALOR" in normalized_text and any(
        token in normalized_text for token in ("HISTORICO", "DESCRICAO", "LANCAMENTOS", "MOVIMENTACOES")
    ):
        evidence.append("statement_transaction_header")
    if any(token in normalized_text for token in ("PIX", "TED", "TARIFA", "DEBITO AUTOMATICO")):
        evidence.append("statement_transaction_signals")
    return evidence


def _has_strong_statement_identity(evidence: list[str]) -> bool:
    evidence_set = set(evidence)
    if "statement_layout" in evidence_set:
        return True
    return "statement_title" in evidence_set and len(evidence_set) >= 3


def _contains_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![A-Z0-9]){re.escape(token)}(?![A-Z0-9])", text) is not None


def _extract_text_preview(extension: str, raw_bytes: bytes) -> str:
    if extension == "pdf":
        return _extract_pdf_text_preview(raw_bytes)
    if extension in {"csv", "ofx", "txt"}:
        return _decode_text_preview(raw_bytes)
    return ""


def _extract_pdf_text_preview(raw_bytes: bytes) -> str:
    try:
        reader = PdfReader(BytesIO(raw_bytes))
        chunks: list[str] = []
        for page in reader.pages[:_PDF_PREVIEW_PAGE_LIMIT]:
            text = page.extract_text() or ""
            if text.strip():
                chunks.append(text)
            if sum(len(item) for item in chunks) >= _PDF_PREVIEW_CHAR_LIMIT:
                break
        return "\n".join(chunks)[:_PDF_PREVIEW_CHAR_LIMIT]
    except Exception:
        return ""


def _decode_text_preview(raw_bytes: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw_bytes.decode(encoding, errors="strict")[:_PDF_PREVIEW_CHAR_LIMIT]
        except UnicodeDecodeError:
            continue
    return ""
