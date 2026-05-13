from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader

from app.application.errors import InvalidFileContentError
from app.application.layout_profiles.registry import DeclarativeLayoutProfile, get_layout_profile
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.normalization.balance import annotate_balance_consistency
from app.application.normalization.canonical import build_canonical_transactions
from app.application.normalization.canonical_metrics import build_canonical_quality_metrics
from app.application.normalization.date import infer_default_statement_year
from app.application.normalization.pdf_amount_tokens import find_amount_tokens, parse_pdf_amount
from app.application.normalization.pdf_columnar_block_rules import next_columnar_block_index
from app.application.normalization.pdf_columnar_row_rules import is_valid_columnar_transaction_row
from app.application.normalization.pdf_columnar_rules import apply_type_sign_hint
from app.application.normalization.pdf_grouped_amount_line_rules import parse_grouped_amount_line
from app.application.normalization.pdf_grouped_date_rules import parse_grouped_date_line
from app.application.normalization.pdf_grouped_line_rules import should_ignore_grouped_line
from app.application.normalization.pdf_grouped_section_rules import resolve_grouped_section_hint
from app.application.normalization.pdf_inline_amount_rules import extract_single_trailing_amount_match
from app.application.normalization.pdf_parse_metrics import build_pdf_parse_metrics
from app.application.normalization.pdf_parser_selection import select_parsed_rows
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.pdf_row_match_rules import (
    is_amount_only_row,
    is_date_only_row,
    match_inline_row,
    match_tabular_date_prefix,
)
from app.application.normalization.pdf_running_balance_rules import parse_running_balance
from app.application.normalization.pdf_signed_amount_rules import compute_hint_signed_amount, compute_tabular_signed_amount
from app.application.normalization.pdf_tabular_profile_rules import resolve_tabular_profile
from app.application.normalization.pdf_tabular_rules import extract_document_reference, select_tabular_amount_token
from app.application.normalization.pdf_text_rules import should_skip_transaction_description
from app.application.normalization.text import normalize_upper_text
from app.application.pdf_layout_inference import PdfLayoutInference, infer_pdf_layout
from app.application.pdf_ocr import PDF_OCR_DISABLED_MESSAGE


@dataclass(frozen=True)
class PdfParseResult:
    transactions: list[NormalizedTransaction]
    layout: PdfLayoutInference
    extracted_text: str
    parse_metrics: dict[str, int | float | str]
    canonical_transactions: list[CanonicalTransaction] | None = None


@dataclass(frozen=True)
class _PdfLine:
    text: str
    page_number: int
    line_number: int


@dataclass(frozen=True)
class _ParsedTransaction:
    transaction: NormalizedTransaction
    source_page: int | None = None
    source_line: int | None = None
    running_balance: float | None = None
    external_reference_id: str | None = None


def parse_pdf_transactions(raw_bytes: bytes) -> PdfParseResult:
    page_texts = _extract_pdf_page_texts(raw_bytes)
    joined_text = "\n".join(page_texts)
    layout = infer_pdf_layout(joined_text)
    layout_profile = get_layout_profile(layout.layout_name)
    lines = _flatten_statement_lines(page_texts)
    grouped_rows = _parse_grouped_statement_lines(lines)
    selection = select_parsed_rows(
        lines=lines,
        grouped_rows=grouped_rows,
        layout_profile=layout_profile,
        parse_inline_rows=_parse_inline_statement_rows,
        parse_tabular_rows=lambda all_lines, profile: _parse_tabular_statement_rows(all_lines, layout_profile=profile),
        parse_columnar_rows=_parse_columnar_statement_blocks,
    )
    parsed_rows = selection.rows
    selected_parser = selection.selected_parser
    inline_candidates = selection.inline_candidates
    inline_transactions_count = selection.inline_transactions_count
    return _build_pdf_parse_result(
        parsed_rows=parsed_rows,
        layout=layout,
        layout_profile=layout_profile,
        selected_parser=selected_parser,
        joined_text=joined_text,
        page_count=len(page_texts),
        flattened_line_count=len(lines),
        grouped_transactions_count=len(grouped_rows),
        inline_candidates_count=inline_candidates,
        inline_transactions_count=inline_transactions_count,
    )


def _build_pdf_parse_result(
    *,
    parsed_rows: list[_ParsedTransaction],
    layout: PdfLayoutInference,
    layout_profile: DeclarativeLayoutProfile | None,
    selected_parser: str,
    joined_text: str,
    page_count: int,
    flattened_line_count: int,
    grouped_transactions_count: int,
    inline_candidates_count: int,
    inline_transactions_count: int,
) -> PdfParseResult:
    transactions = [item.transaction for item in parsed_rows]
    canonical_transactions = build_canonical_transactions(
        parsed_rows,
        bank_name=layout_profile.bank if layout_profile is not None else None,
        layout_name=layout.layout_name,
        layout_used_fallback=layout.used_fallback,
        layout_confidence=layout.confidence,
        source_parser=selected_parser,
    )
    balance_checked_count, balance_failed_count = annotate_balance_consistency(canonical_transactions)
    canonical_quality_metrics = build_canonical_quality_metrics(canonical_transactions)

    return PdfParseResult(
        transactions=transactions,
        canonical_transactions=canonical_transactions,
        layout=layout,
        extracted_text=joined_text,
        parse_metrics=build_pdf_parse_metrics(
            page_count=page_count,
            extracted_char_count=len(joined_text),
            flattened_line_count=flattened_line_count,
            grouped_transactions_count=grouped_transactions_count,
            inline_candidates_count=inline_candidates_count,
            inline_transactions_count=inline_transactions_count,
            selected_parser=selected_parser,
            balance_consistency_checked=balance_checked_count,
            balance_consistency_failed=balance_failed_count,
            canonical_quality_metrics=canonical_quality_metrics,
        ),
    )


def _extract_pdf_page_texts(raw_bytes: bytes) -> list[str]:
    pages = _read_native_pdf_page_texts(raw_bytes)
    if pages:
        return pages

    raise InvalidFileContentError(PDF_OCR_DISABLED_MESSAGE)


def _read_native_pdf_page_texts(raw_bytes: bytes) -> list[str]:
    try:
        reader = PdfReader(BytesIO(raw_bytes))
    except Exception as exc:  # pragma: no cover - defensive guard for parser internals
        raise InvalidFileContentError("Unable to read PDF bytes.") from exc

    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    pages = [item for item in pages if item]
    return pages


def _flatten_statement_lines(page_texts: list[str]) -> list[_PdfLine]:
    lines: list[_PdfLine] = []
    for page_index, page_text in enumerate(page_texts):
        for line_index, line in enumerate(page_text.splitlines()):
            cleaned = " ".join(line.split())
            if cleaned:
                lines.append(_PdfLine(text=cleaned, page_number=page_index + 1, line_number=line_index + 1))
    return lines


def _parse_grouped_statement_lines(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    transactions: list[_ParsedTransaction] = []
    current_date: str | None = None
    current_section_hint: str | None = None
    description_parts: list[str] = []
    inferred_year = _infer_default_statement_year_from_lines(lines)

    for line in lines:
        normalized_line = _normalize_text(line.text)
        grouped_date_match = parse_grouped_date_line(normalized_line, inferred_year=inferred_year)
        if grouped_date_match is not None:
            current_date = grouped_date_match.date
            current_section_hint = None
            description_parts = []
            rest = grouped_date_match.rest
            current_section_hint = resolve_grouped_section_hint(rest, current_hint=current_section_hint)
            inline_transaction = _build_inline_transaction_from_date_rest(
                date=current_date,
                rest=rest,
                section_hint=current_section_hint,
                source_page=line.page_number,
                source_line=line.line_number,
            )
            if inline_transaction is not None:
                transactions.append(inline_transaction)
                description_parts = []
            continue

        if current_date is None:
            continue

        updated_hint = resolve_grouped_section_hint(normalized_line, current_hint=current_section_hint)
        if updated_hint != current_section_hint:
            current_section_hint = updated_hint
            description_parts = []
            continue

        if should_ignore_grouped_line(normalized_line):
            description_parts = []
            continue

        if is_amount_only_row(line.text):
            parsed_row = _build_grouped_amount_only_transaction(
                date=current_date,
                description_parts=description_parts,
                line=line,
                section_hint=current_section_hint,
            )
            if parsed_row is None:
                continue
            transactions.append(parsed_row)
            description_parts = []
            continue

        description_parts.append(line.text.strip())

    return transactions


def _parse_inline_statement_rows(lines: list[_PdfLine]) -> tuple[list[_ParsedTransaction], int]:
    transactions: list[_ParsedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year_from_lines(lines)

    for line in lines:
        match = match_inline_row(line.text)
        if not match:
            continue

        rest = match.group("rest").strip()
        amount_match = extract_single_trailing_amount_match(rest)
        if amount_match is None:
            continue

        raw_description = amount_match.description
        if not raw_description or should_skip_transaction_description(raw_description):
            continue

        candidates += 1

        amount = parse_pdf_amount(amount_match.amount_token.value)
        signed_amount = compute_hint_signed_amount(raw_amount=amount, description=raw_description)
        transactions.append(
            _build_parsed_transaction(
                date=parse_row_date(match.group("date"), fallback_year=inferred_year),
                description=raw_description,
                amount=signed_amount,
                source_page=line.page_number,
                source_line=line.line_number,
            )
        )

    return transactions, candidates


def _parse_tabular_statement_rows(
    lines: list[_PdfLine], *, layout_profile: DeclarativeLayoutProfile | None = None
) -> tuple[list[_ParsedTransaction], int]:
    transactions: list[_ParsedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year_from_lines(lines)
    line_texts = _extract_line_texts(lines)
    tabular_profile = resolve_tabular_profile(line_texts, layout_profile=layout_profile)

    for line in lines:
        match = match_tabular_date_prefix(line.text)
        if not match:
            continue

        rest = match.group("rest").strip()
        if not rest:
            continue

        amount_tokens = find_amount_tokens(rest)
        if not amount_tokens:
            continue
        candidates += 1

        selected_amount = select_tabular_amount_token(amount_tokens, layout_profile=tabular_profile)
        if selected_amount is None:
            continue

        raw_description = rest[: selected_amount.description_end].strip()
        if not raw_description or should_skip_transaction_description(raw_description):
            continue

        external_reference_id = extract_document_reference(raw_description, layout_profile=tabular_profile)

        running_balance = parse_running_balance(selected_amount.balance_token)

        signed_amount = compute_tabular_signed_amount(
            raw_amount=parse_pdf_amount(selected_amount.token.value),
            role=selected_amount.role,
            description=raw_description,
        )
        transactions.append(
            _build_parsed_transaction(
                date=parse_row_date(match.group("date"), fallback_year=inferred_year),
                description=raw_description,
                amount=signed_amount,
                source_page=line.page_number,
                source_line=line.line_number,
                running_balance=running_balance,
                external_reference_id=external_reference_id,
            )
        )

    return transactions, candidates

def _parse_columnar_statement_blocks(lines: list[_PdfLine]) -> tuple[list[_ParsedTransaction], int]:
    transactions: list[_ParsedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year_from_lines(lines)
    line_texts = _extract_line_texts(lines)
    index = 0

    while index < len(lines):
        parsed_row = _parse_columnar_block_at_index(lines=lines, index=index, inferred_year=inferred_year)
        if parsed_row is None:
            index += 1
            continue

        candidates += 1
        transactions.append(parsed_row)
        index = next_columnar_block_index(line_texts, current_index=index)

    return transactions, candidates


def _parse_columnar_block_at_index(
    *, lines: list[_PdfLine], index: int, inferred_year: int | None
) -> _ParsedTransaction | None:
    raw_date = lines[index].text.strip()
    if not is_date_only_row(raw_date):
        return None

    if index + 3 >= len(lines):
        return None

    description = lines[index + 1].text.strip()
    type_raw = lines[index + 2].text.strip()
    amount_raw = lines[index + 3].text.strip()
    if not is_valid_columnar_transaction_row(description=description, type_raw=type_raw, amount_raw=amount_raw):
        return None

    amount = apply_type_sign_hint(parse_pdf_amount(amount_raw), type_raw)
    signed_amount = compute_hint_signed_amount(raw_amount=amount, description=description)
    return _build_parsed_transaction(
        date=parse_row_date(raw_date, fallback_year=inferred_year),
        description=description,
        amount=signed_amount,
        source_page=lines[index].page_number,
        source_line=lines[index].line_number,
    )


def _build_grouped_amount_only_transaction(
    *,
    date: str,
    description_parts: list[str],
    line: _PdfLine,
    section_hint: str | None,
) -> _ParsedTransaction | None:
    if not description_parts:
        return None

    description = " ".join(description_parts).strip()
    signed_amount = parse_grouped_amount_line(
        raw_amount_text=line.text,
        description=description,
        section_hint=section_hint,
    )
    return _build_parsed_transaction(
        date=date,
        description=description,
        amount=signed_amount,
        source_page=line.page_number,
        source_line=line.line_number,
    )


def _build_inline_transaction_from_date_rest(
    *,
    date: str,
    rest: str,
    section_hint: str | None,
    source_page: int | None = None,
    source_line: int | None = None,
) -> _ParsedTransaction | None:
    amount_match = extract_single_trailing_amount_match(rest)
    if amount_match is None:
        return None

    raw_description = amount_match.description
    if not raw_description or should_skip_transaction_description(raw_description):
        return None
    amount = parse_pdf_amount(amount_match.amount_token.value)
    signed_amount = compute_hint_signed_amount(raw_amount=amount, description=raw_description, section_hint=section_hint)
    return _build_parsed_transaction(
        date=date,
        description=raw_description,
        amount=signed_amount,
        source_page=source_page,
        source_line=source_line,
    )


def _build_parsed_transaction(
    *,
    date: str,
    description: str,
    amount: float,
    source_page: int | None = None,
    source_line: int | None = None,
    running_balance: float | None = None,
    external_reference_id: str | None = None,
) -> _ParsedTransaction:
    return _ParsedTransaction(
        transaction=NormalizedTransaction(
            date=date,
            description=description,
            amount=amount,
            type="inflow" if amount >= 0 else "outflow",
        ),
        source_page=source_page,
        source_line=source_line,
        running_balance=running_balance,
        external_reference_id=external_reference_id,
    )


def _normalize_text(value: str) -> str:
    return normalize_upper_text(value)


def _infer_default_statement_year_from_lines(lines: list[_PdfLine]) -> int | None:
    return infer_default_statement_year(_extract_line_texts(lines))


def _extract_line_texts(lines: list[_PdfLine]) -> list[str]:
    return [line.text for line in lines]

