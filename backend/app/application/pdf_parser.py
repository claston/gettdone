import re
from dataclasses import dataclass
from datetime import datetime
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
    parsed_rows = _adjust_forward_year_rollover_rows(parsed_rows, line_texts=_extract_line_texts(lines))
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


def _adjust_forward_year_rollover_rows(
    parsed_rows: list[_ParsedTransaction], *, line_texts: list[str]
) -> list[_ParsedTransaction]:
    if len(parsed_rows) < 2:
        return parsed_rows
    if not _has_explicit_adjacent_year_hints(line_texts):
        return parsed_rows

    adjusted_rows: list[_ParsedTransaction] = [parsed_rows[0]]
    previous_date = datetime.strptime(parsed_rows[0].transaction.date, "%Y-%m-%d")

    for row in parsed_rows[1:]:
        current_date = datetime.strptime(row.transaction.date, "%Y-%m-%d")
        should_roll_forward = (
            current_date.year == previous_date.year
            and previous_date.month == 12
            and current_date.month == 1
            and current_date < previous_date
        )
        if should_roll_forward:
            rolled_date = current_date.replace(year=current_date.year + 1)
            rolled_transaction = NormalizedTransaction(
                date=rolled_date.strftime("%Y-%m-%d"),
                description=row.transaction.description,
                amount=row.transaction.amount,
                type=row.transaction.type,
            )
            adjusted_row = _ParsedTransaction(
                transaction=rolled_transaction,
                source_page=row.source_page,
                source_line=row.source_line,
                running_balance=row.running_balance,
                external_reference_id=row.external_reference_id,
            )
            adjusted_rows.append(adjusted_row)
            previous_date = rolled_date
            continue

        adjusted_rows.append(row)
        previous_date = current_date

    return adjusted_rows


def _has_explicit_adjacent_year_hints(line_texts: list[str]) -> bool:
    years: set[int] = set()
    for line in line_texts:
        for raw in re.findall(r"\b\d{4}\b", line):
            year = int(raw)
            if 1900 <= year <= 2100:
                years.add(year)
    if len(years) < 2:
        return False
    sorted_years = sorted(years)
    return any(current - previous == 1 for previous, current in zip(sorted_years, sorted_years[1:]))


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
            current_date, current_section_hint, description_parts, inline_transaction = _parse_grouped_date_line_state(
                line=line,
                grouped_date=grouped_date_match.date,
                grouped_rest=grouped_date_match.rest,
            )
            if inline_transaction is not None:
                transactions.append(inline_transaction)
                description_parts = []
            continue

        if current_date is None:
            continue

        current_section_hint, description_parts, should_continue = _update_grouped_section_state(
            normalized_line=normalized_line,
            current_section_hint=current_section_hint,
            description_parts=description_parts,
        )
        if should_continue:
            continue

        description_parts, should_continue = _handle_grouped_ignored_line(
            normalized_line=normalized_line,
            description_parts=description_parts,
        )
        if should_continue:
            continue

        parsed_row, description_parts, should_continue = _handle_grouped_amount_only_line(
            current_date=current_date,
            description_parts=description_parts,
            line=line,
            section_hint=current_section_hint,
        )
        if should_continue:
            if parsed_row is not None:
                transactions.append(parsed_row)
            continue

        description_parts = _append_grouped_description_part(
            description_parts=description_parts,
            raw_text=line.text,
        )

    return transactions


def _parse_inline_statement_rows(lines: list[_PdfLine]) -> tuple[list[_ParsedTransaction], int]:
    transactions: list[_ParsedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year_from_lines(lines)

    for line in lines:
        parsed_row = _parse_inline_statement_line(line=line, inferred_year=inferred_year)
        if parsed_row is None:
            continue
        candidates += 1
        transactions.append(parsed_row)

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
        parsed_row, is_candidate = _classify_tabular_statement_line(
            line=line,
            inferred_year=inferred_year,
            tabular_profile=tabular_profile,
        )
        candidates = _accumulate_tabular_row(
            transactions=transactions,
            parsed_row=parsed_row,
            is_candidate=is_candidate,
            candidates=candidates,
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
        if parsed_row is not None:
            candidates += 1
            transactions.append(parsed_row)
        index = _resolve_next_columnar_index(line_texts=line_texts, current_index=index, parsed_row=parsed_row)

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


def _parse_grouped_date_line_state(
    *,
    line: _PdfLine,
    grouped_date: str,
    grouped_rest: str,
) -> tuple[str, str | None, list[str], _ParsedTransaction | None]:
    next_date = grouped_date
    next_description_parts: list[str] = []
    next_section_hint = resolve_grouped_section_hint(grouped_rest, current_hint=None)
    inline_transaction = _build_inline_transaction_from_date_rest(
        date=next_date,
        rest=grouped_rest,
        section_hint=next_section_hint,
        source_page=line.page_number,
        source_line=line.line_number,
    )
    return next_date, next_section_hint, next_description_parts, inline_transaction


def _resolve_next_columnar_index(
    *, line_texts: list[str], current_index: int, parsed_row: _ParsedTransaction | None
) -> int:
    if parsed_row is None:
        return current_index + 1
    return next_columnar_block_index(line_texts, current_index=current_index)


def _parse_inline_statement_line(*, line: _PdfLine, inferred_year: int | None) -> _ParsedTransaction | None:
    match = match_inline_row(line.text)
    if not match:
        return None

    rest = match.group("rest").strip()
    amount_match = extract_single_trailing_amount_match(rest)
    if amount_match is None:
        return None

    raw_description = amount_match.description
    if not raw_description or should_skip_transaction_description(raw_description):
        return None

    amount = parse_pdf_amount(amount_match.amount_token.value)
    signed_amount = compute_hint_signed_amount(raw_amount=amount, description=raw_description)
    return _build_parsed_transaction(
        date=parse_row_date(match.group("date"), fallback_year=inferred_year),
        description=raw_description,
        amount=signed_amount,
        source_page=line.page_number,
        source_line=line.line_number,
    )


def _parse_tabular_statement_line(
    *,
    line: _PdfLine,
    inferred_year: int | None,
    tabular_profile: DeclarativeLayoutProfile | None,
) -> _ParsedTransaction | None:
    parsed_row, _ = _classify_tabular_statement_line(
        line=line,
        inferred_year=inferred_year,
        tabular_profile=tabular_profile,
    )
    return parsed_row


def _classify_tabular_statement_line(
    *,
    line: _PdfLine,
    inferred_year: int | None,
    tabular_profile: DeclarativeLayoutProfile | None,
) -> tuple[_ParsedTransaction | None, bool]:
    match = match_tabular_date_prefix(line.text)
    if not match:
        return None, False

    rest = match.group("rest").strip()
    if not rest:
        return None, False

    amount_tokens = find_amount_tokens(rest)
    if not amount_tokens:
        return None, False

    selected_amount = select_tabular_amount_token(amount_tokens, layout_profile=tabular_profile)
    if selected_amount is None:
        return None, True

    raw_description = rest[: selected_amount.description_end].strip()
    if not raw_description or should_skip_transaction_description(raw_description):
        return None, True

    external_reference_id = extract_document_reference(raw_description, layout_profile=tabular_profile)
    amount_details = _build_tabular_amount_details(
        amount_token_value=selected_amount.token.value,
        selected_role=selected_amount.role,
        raw_description=raw_description,
        balance_token_value=selected_amount.balance_token.value if selected_amount.balance_token else None,
    )
    return (
        _build_parsed_transaction(
            date=parse_row_date(match.group("date"), fallback_year=inferred_year),
            description=raw_description,
            amount=amount_details["signed_amount"],
            source_page=line.page_number,
            source_line=line.line_number,
            running_balance=amount_details["running_balance"],
            external_reference_id=external_reference_id,
        ),
        True,
    )


def _accumulate_tabular_row(
    *,
    transactions: list[_ParsedTransaction],
    parsed_row: _ParsedTransaction | None,
    is_candidate: bool,
    candidates: int,
) -> int:
    next_candidates = candidates + 1 if is_candidate else candidates
    if parsed_row is not None:
        transactions.append(parsed_row)
    return next_candidates


def _build_tabular_amount_details(
    *,
    amount_token_value: str,
    selected_role: str,
    raw_description: str,
    balance_token_value: str | None,
) -> dict[str, float | None]:
    signed_amount = compute_tabular_signed_amount(
        raw_amount=parse_pdf_amount(amount_token_value),
        role=selected_role,
        description=raw_description,
    )
    running_balance = parse_pdf_amount(balance_token_value) if balance_token_value is not None else None
    return {"signed_amount": signed_amount, "running_balance": running_balance}


def _update_grouped_section_state(
    *,
    normalized_line: str,
    current_section_hint: str | None,
    description_parts: list[str],
) -> tuple[str | None, list[str], bool]:
    next_hint = resolve_grouped_section_hint(normalized_line, current_hint=current_section_hint)
    if next_hint != current_section_hint:
        return next_hint, [], True
    return next_hint, description_parts, False


def _handle_grouped_ignored_line(*, normalized_line: str, description_parts: list[str]) -> tuple[list[str], bool]:
    if should_ignore_grouped_line(normalized_line):
        return [], True
    return description_parts, False


def _handle_grouped_amount_only_line(
    *,
    current_date: str,
    description_parts: list[str],
    line: _PdfLine,
    section_hint: str | None,
) -> tuple[_ParsedTransaction | None, list[str], bool]:
    if not is_amount_only_row(line.text):
        return None, description_parts, False

    parsed_row = _build_grouped_amount_only_transaction(
        date=current_date,
        description_parts=description_parts,
        line=line,
        section_hint=section_hint,
    )
    if parsed_row is None:
        return None, description_parts, True
    return parsed_row, [], True


def _append_grouped_description_part(*, description_parts: list[str], raw_text: str) -> list[str]:
    cleaned_text = raw_text.strip()
    if not cleaned_text:
        return description_parts
    return [*description_parts, cleaned_text]


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

