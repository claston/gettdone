import re
from dataclasses import dataclass

from app.application.document_type_classifier import BANK_RECEIPT, BANK_STATEMENT, FISCAL_INVOICE, UNKNOWN_DOCUMENT, classify_document_type
from app.application.errors import UnsupportedDocumentContentError
from app.application.normalization.text import normalize_upper_text


@dataclass(frozen=True)
class SegmentedPdf:
    page_texts: tuple[str, ...]
    source_page_texts: tuple[str, ...]
    parts: list[dict[str, object]]


def segment_pdf_pages(page_texts: list[str] | tuple[str, ...]) -> SegmentedPdf:
    """Use existing text only; blank excluded pages so transaction provenance stays stable."""
    source = tuple(page_texts)
    classifications = [classify_document_type("page.pdf", b"", extracted_text=text) for text in source]
    kinds = [item.document_type for item in classifications]
    identities = {
        identity for text, kind in zip(source, kinds, strict=True)
        if kind == BANK_STATEMENT and (identity := _statement_identity(text))
    }
    if len(identities) > 1:
        raise UnsupportedDocumentContentError(
            document_type="multiple_bank_statements",
            message=("Este PDF contém extratos de contas ou períodos diferentes. "
                     "Envie cada extrato separadamente para evitar misturar lançamentos."),
        )
    has_statement = BANK_STATEMENT in kinds
    has_annex = any(kind in {FISCAL_INVOICE, BANK_RECEIPT} for kind in kinds)
    if not has_statement or not has_annex:
        return SegmentedPdf(source, source, [])

    parts: list[dict[str, object]] = []
    filtered: list[str] = []
    previous_kind = None
    for index, (text, classification) in enumerate(zip(source, classifications, strict=True), start=1):
        kind = classification.document_type
        continuation = kind == UNKNOWN_DOCUMENT and previous_kind == BANK_STATEMENT and _has_statement_rows(text)
        if continuation:
            kind = BANK_STATEMENT
        mixed_page = kind == FISCAL_INVOICE and _has_statement_title(text)
        included = kind == BANK_STATEMENT and not mixed_page
        if mixed_page:
            kind = "mixed"
        filtered.append(text if included else "")
        # Group by type and consecutive pages. These are page groups, not invoice counts.
        if parts and parts[-1]["document_type"] == kind and parts[-1]["included_in_statement"] == included:
            parts[-1]["pages"].append(index)
        else:
            parts.append({
                "id": f"part-{len(parts) + 1}", "document_type": kind, "pages": [index],
                "included_in_statement": included,
                "requires_review": kind in {UNKNOWN_DOCUMENT, "mixed"},
            })
        previous_kind = kind
    return SegmentedPdf(tuple(filtered), source, parts)


def _has_statement_rows(text: str) -> bool:
    normalized = normalize_upper_text(text)
    return bool(re.search(r"(?m)^\s*\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", normalized)) and bool(
        re.search(r"\d[\d.]*,\d{2}", normalized)
    ) and any(token in normalized for token in ("PIX", "SALDO", "TED", "TARIFA", "TRANSFERENCIA"))


def _has_statement_title(text: str) -> bool:
    normalized = normalize_upper_text(text)
    return bool(re.search(r"EXTRATO\s+(?:MENSAL|BANCARIO|DE CONTA)", normalized)) and "SALDO" in normalized


def _statement_identity(text: str) -> str | None:
    normalized = normalize_upper_text(text)
    account = re.search(r"\b(?:CC|C/C|CONTA(?: CORRENTE)?)\s*[:.]?\s*(\d[\d.]*[-/]\d+|\d{4,})(?![\d,.])\b", normalized)
    if account is None:
        return None
    agency = re.search(r"\b(?:AG|AGENCIA)\s*[:.]?\s*(\d{3,6}(?:-\d)?)", normalized)
    period = re.search(r"\b(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+20\d{2}\b", normalized)
    return "|".join((agency.group(1) if agency else "", account.group(1), period.group(0) if period else ""))
