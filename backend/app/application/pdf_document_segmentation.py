import re
from dataclasses import dataclass

from app.application.document_type_classifier import BANK_RECEIPT, BANK_STATEMENT, FISCAL_INVOICE, UNKNOWN_DOCUMENT, classify_document_type
from app.application.errors import UnsupportedDocumentContentError
from app.application.financial_document_detection import FISCAL_SUMMARY, INVESTMENT_STATEMENT, RECEIVABLES_REPORT
from app.application.normalization.text import normalize_upper_text

_DATE_PATTERN = (
    r"(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}\s+(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+20\d{2})\b"
)
_MOVEMENT_DATE_PREFIX = re.compile(rf"^{_DATE_PATTERN}")
_PERIOD_DATE_RANGE = re.compile(rf"^{_DATE_PATTERN}\s+(?:A|ATE|[-–])\s+{_DATE_PATTERN}")
_MOVEMENT_HEADER = re.compile(r"^DATA(?:\s+(?:DESCRICAO|HISTORICO|DOCUMENTO|VALOR|SALDO)\b|$)")


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
    annex_types = {FISCAL_INVOICE, BANK_RECEIPT, FISCAL_SUMMARY, INVESTMENT_STATEMENT, RECEIVABLES_REPORT}
    has_annex = any(kind in annex_types for kind in kinds)
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
    normalized = _statement_header(text)
    columns = re.search(
        r"\bAGENCIA\s+CONTA(?: CORRENTE)?\s+(\d{3,6}(?:-\d)?)\s+"
        r"(\d[\d.]*[-/]\d+|\d{4,})(?![\d,.])\b", normalized,
    )
    if columns:
        period = re.search(r"\b(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+20\d{2}\b", normalized)
        return "|".join((columns[1], columns[2], period[0] if period else ""))
    account = re.search(r"\b(?:CC|C/C|CONTA(?: CORRENTE)?)\s*[:.]?\s*(\d[\d.]*[-/]\d+|\d{4,})(?![\d,.])\b", normalized)
    if account is None:
        return None
    agency = re.search(r"\b(?:AG|AGENCIA)\s*[:.]?\s*(\d{3,6}(?:-\d)?)", normalized)
    if agency is None:
        # Native Nubank exports put the agency value before the column labels.
        agency = re.search(r"(?<![\d./-])(\d{3,6}(?:-\d)?)\s+(?:(?:CPF|CNPJ)\s+)?AGENCIA\s+CONTA\b", normalized)
    period = re.search(r"\b(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+20\d{2}\b", normalized)
    return "|".join((agency.group(1) if agency else "", account.group(1), period.group(0) if period else ""))


def _statement_header(text: str) -> str:
    """Keep counterparty bank details out of the statement owner's identity."""
    header = []
    for raw_line in text.splitlines():
        line = normalize_upper_text(raw_line)
        movement_date = _MOVEMENT_DATE_PREFIX.match(line) and not _PERIOD_DATE_RANGE.match(line)
        if movement_date or _MOVEMENT_HEADER.match(line):
            break
        header.append(line)
        # Nubank repeats this period/currency header even on continuation pages.
        if "VALORES EM R$" in line:
            break
    return " ".join(header)
