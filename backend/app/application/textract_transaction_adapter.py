import re
import unicodedata
from dataclasses import dataclass

from app.application.document_extraction_models import RawDocumentExtraction
from app.application.errors import InvalidFileContentError
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.normalization.pdf_amount_tokens import find_amount_tokens, parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date


@dataclass(frozen=True)
class TextractTransactionExtractionResult:
    transactions: list[NormalizedTransaction]
    canonical_transactions: list[CanonicalTransaction]
    extracted_text: str
    parse_metrics: dict[str, int | float | str]


def adapt_textract_extraction_to_transactions(extraction: RawDocumentExtraction) -> TextractTransactionExtractionResult:
    canonical = _from_header_tables(extraction)
    parser = "textract_table"
    if not canonical:
        canonical = _from_table_rows(extraction)
        parser = "textract_table_row"
    if not canonical:
        canonical = _from_line_windows(extraction)
        parser = "textract_line_window"
    if not canonical:
        raise InvalidFileContentError("Nao foi possivel extrair transacoes do OCR para revisao.")

    transactions = [
        NormalizedTransaction(
            date=item.date,
            description=item.description,
            amount=item.amount,
            type=item.type,
        )
        for item in canonical
    ]
    extracted_text = "\n".join(
        line.text
        for page in extraction.pages
        for line in sorted(page.lines, key=lambda value: value.line_index)
        if line.text
    )
    return TextractTransactionExtractionResult(
        transactions=transactions,
        canonical_transactions=canonical,
        extracted_text=extracted_text,
        parse_metrics={
            "selected_parser": parser,
            "transaction_count": len(canonical),
        },
    )


def _from_header_tables(extraction: RawDocumentExtraction) -> list[CanonicalTransaction]:
    rows: list[CanonicalTransaction] = []
    for page in extraction.pages:
        for table in page.tables:
            if len(table.rows) < 2:
                continue
            header = [cell.text for cell in table.rows[0]]
            mapping = _resolve_header_mapping(header)
            if mapping is None:
                continue
            for row_index, row in enumerate(table.rows[1:], start=2):
                date_raw = row[mapping["date"] - 1].text.strip()
                description = row[mapping["description"] - 1].text.strip()
                if not date_raw or not description:
                    continue
                amount = _resolve_amount_from_row(row, mapping)
                if amount is None:
                    continue
                rows.append(
                    CanonicalTransaction(
                        date=parse_row_date(date_raw, fallback_year=None),
                        description=description,
                        amount=amount,
                        type="inflow" if amount >= 0 else "outflow",
                        source_page=page.page_number,
                        source_line=row_index,
                        source_parser="textract_table",
                        running_balance=_resolve_balance(row, mapping),
                        external_reference_id=_optional_cell(row, mapping, "document"),
                        warnings=[],
                        confidence=_row_confidence(row),
                    )
                )
    return rows


def _from_table_rows(extraction: RawDocumentExtraction) -> list[CanonicalTransaction]:
    rows: list[CanonicalTransaction] = []
    for page in extraction.pages:
        for table in page.tables:
            for row_index, row in enumerate(table.rows, start=1):
                text = " ".join(cell.text.strip() for cell in row if cell.text.strip())
                dates = re.findall(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", text)
                amounts = find_amount_tokens(text)
                if not dates or not amounts:
                    continue
                amount = parse_pdf_amount(amounts[-1].value)
                rows.append(
                    CanonicalTransaction(
                        date=parse_row_date(dates[0], fallback_year=None),
                        description=text,
                        amount=amount,
                        type="inflow" if amount >= 0 else "outflow",
                        source_page=page.page_number,
                        source_line=row_index,
                        source_parser="textract_table_row",
                        warnings=["textract_table_row_candidate", "textract_layout_inferred"],
                        confidence=_row_confidence(row),
                    )
                )
    return rows


def _from_line_windows(extraction: RawDocumentExtraction) -> list[CanonicalTransaction]:
    rows: list[CanonicalTransaction] = []
    for page in extraction.pages:
        lines = sorted(page.lines, key=lambda value: value.line_index)
        index = 0
        while index < len(lines):
            current = lines[index]
            dates = re.findall(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b", current.text)
            if not dates:
                index += 1
                continue
            window = [current]
            for next_line in lines[index + 1 : index + 5]:
                window.append(next_line)
                joined = " ".join(item.text for item in window)
                if find_amount_tokens(joined):
                    break
            joined = " ".join(item.text for item in window).strip()
            amounts = find_amount_tokens(joined)
            if not amounts:
                index += 1
                continue
            amount = parse_pdf_amount(amounts[-1].value)
            confidence_values = [line.confidence for line in window if isinstance(line.confidence, float)]
            rows.append(
                CanonicalTransaction(
                    date=parse_row_date(dates[0], fallback_year=None),
                    description=joined,
                    amount=amount,
                    type="inflow" if amount >= 0 else "outflow",
                    source_page=page.page_number,
                    source_line=current.line_index,
                    source_parser="textract_line_window",
                    warnings=["textract_line_window_candidate", "manual_review_recommended"],
                    confidence=round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else None,
                )
            )
            index += len(window)
    return rows


def _resolve_header_mapping(header: list[str]) -> dict[str, int] | None:
    normalized = [_normalize_header(value) for value in header]

    def _find(*needles: str) -> int | None:
        for index, value in enumerate(normalized, start=1):
            if any(needle in value for needle in needles):
                return index
        return None

    date_col = _find("data")
    description_col = _find("descricao", "historico", "lancamento")
    credit_col = _find("credito")
    debit_col = _find("debito")
    amount_col = _find("valor")
    if date_col is None or description_col is None:
        return None
    if credit_col is None and debit_col is None and amount_col is None:
        return None
    mapping: dict[str, int] = {"date": date_col, "description": description_col}
    if credit_col is not None:
        mapping["credit"] = credit_col
    if debit_col is not None:
        mapping["debit"] = debit_col
    if amount_col is not None:
        mapping["amount"] = amount_col
    balance_col = _find("saldo")
    document_col = _find("doc", "dcto", "documento")
    if balance_col is not None:
        mapping["balance"] = balance_col
    if document_col is not None:
        mapping["document"] = document_col
    return mapping


def _resolve_amount_from_row(row, mapping: dict[str, int]) -> float | None:
    if "credit" in mapping:
        credit = row[mapping["credit"] - 1].text.strip()
        if credit:
            return abs(parse_pdf_amount(credit))
    if "debit" in mapping:
        debit = row[mapping["debit"] - 1].text.strip()
        if debit:
            return -abs(parse_pdf_amount(debit))
    if "amount" in mapping:
        raw = row[mapping["amount"] - 1].text.strip()
        if raw:
            return parse_pdf_amount(raw)
    return None


def _resolve_balance(row, mapping: dict[str, int]) -> float | None:
    if "balance" not in mapping:
        return None
    value = row[mapping["balance"] - 1].text.strip()
    if not value:
        return None
    return parse_pdf_amount(value)


def _optional_cell(row, mapping: dict[str, int], field: str) -> str | None:
    if field not in mapping:
        return None
    value = row[mapping[field] - 1].text.strip()
    return value or None


def _row_confidence(row) -> float | None:
    values = [cell.confidence for cell in row if isinstance(cell.confidence, float)]
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _normalize_header(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    return "".join(char for char in normalized if not unicodedata.combining(char))
