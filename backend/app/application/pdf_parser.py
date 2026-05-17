import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Callable

from pypdf import PdfReader

from app.application.errors import InvalidFileContentError
from app.application.layout_profiles.registry import DeclarativeLayoutProfile, get_layout_profile
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.normalization.balance import annotate_balance_consistency
from app.application.normalization.canonical import build_canonical_transactions
from app.application.normalization.canonical_metrics import build_canonical_quality_metrics
from app.application.normalization.date import infer_default_statement_year
from app.application.normalization.pdf_amount_tokens import find_amount_tokens, has_explicit_amount_sign, parse_pdf_amount
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
from app.application.normalization.pdf_text_rules import should_ignore_line, should_skip_transaction_description
from app.application.normalization.text import normalize_upper_text
from app.application.pdf_layout_inference import PdfLayoutInference, infer_pdf_layout
from app.application.pdf_ocr import PDF_OCR_DISABLED_MESSAGE, extract_pdf_page_texts_with_ocr, is_pdf_ocr_enabled


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
    has_explicit_amount_sign: bool = False


def parse_pdf_transactions(
    raw_bytes: bytes,
    on_ocr_progress: Callable[[int, int], None] | None = None,
) -> PdfParseResult:
    if on_ocr_progress is None:
        page_texts = _extract_pdf_page_texts(raw_bytes)
    else:
        page_texts = _extract_pdf_page_texts(raw_bytes, on_ocr_progress)
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
    tabular_candidates_count = selection.tabular_candidates
    tabular_transactions_count = selection.tabular_transactions_count
    columnar_candidates_count = selection.columnar_candidates
    columnar_transactions_count = selection.columnar_transactions_count
    parser_selection_reason = selection.selection_reason
    inline_decision = selection.inline_decision
    tabular_decision = selection.tabular_decision
    columnar_decision = selection.columnar_decision
    return _build_pdf_parse_result(
        parsed_rows=parsed_rows,
        layout=layout,
        layout_profile=layout_profile,
        selected_parser=selected_parser,
        parser_selection_reason=parser_selection_reason,
        inline_decision=inline_decision,
        tabular_decision=tabular_decision,
        columnar_decision=columnar_decision,
        joined_text=joined_text,
        page_count=len(page_texts),
        flattened_line_count=len(lines),
        grouped_transactions_count=len(grouped_rows),
        inline_candidates_count=inline_candidates,
        inline_transactions_count=inline_transactions_count,
        tabular_candidates_count=tabular_candidates_count,
        tabular_transactions_count=tabular_transactions_count,
        columnar_candidates_count=columnar_candidates_count,
        columnar_transactions_count=columnar_transactions_count,
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
    parser_selection_reason: str,
    inline_decision: str,
    tabular_decision: str,
    columnar_decision: str,
    joined_text: str,
    page_count: int,
    flattened_line_count: int,
    grouped_transactions_count: int,
    inline_candidates_count: int,
    inline_transactions_count: int,
    tabular_candidates_count: int,
    tabular_transactions_count: int,
    columnar_candidates_count: int,
    columnar_transactions_count: int,
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
              tabular_candidates_count=tabular_candidates_count,
              tabular_transactions_count=tabular_transactions_count,
              columnar_candidates_count=columnar_candidates_count,
              columnar_transactions_count=columnar_transactions_count,
              selected_parser=selected_parser,
              parser_selection_reason=parser_selection_reason,
              inline_decision=inline_decision,
              tabular_decision=tabular_decision,
              columnar_decision=columnar_decision,
              balance_consistency_checked=balance_checked_count,
              balance_consistency_failed=balance_failed_count,
              canonical_quality_metrics=canonical_quality_metrics,
        ),
    )


def _extract_pdf_page_texts(
    raw_bytes: bytes,
    on_ocr_progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    pages = _read_native_pdf_page_texts(raw_bytes)
    if pages:
        return pages
    if is_pdf_ocr_enabled():
        if on_ocr_progress is None:
            ocr_pages = extract_pdf_page_texts_with_ocr(raw_bytes)
        else:
            ocr_pages = extract_pdf_page_texts_with_ocr(raw_bytes, on_ocr_progress)
        if ocr_pages:
            return ocr_pages

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
    last_transaction_index: int | None = None
    last_known_running_balance: float | None = None
    pending_opening_balance_label: str | None = None
    pending_opening_balance_line: _PdfLine | None = None
    opening_balance_amount: float | None = None
    opening_balance_amount_line: _PdfLine | None = None
    opening_balance_inserted = False

    for line in lines:
        normalized_line = _normalize_text(line.text)
        if normalized_line.startswith("SALDO ANTERIOR") or normalized_line.startswith("SALDO INICIAL"):
            pending_opening_balance_label = "SALDO ANTERIOR" if "ANTERIOR" in normalized_line else "SALDO INICIAL"
            pending_opening_balance_line = line
            current_date = None
            description_parts = []
            continue
        if (
            pending_opening_balance_label is not None
            and opening_balance_amount is None
            and current_date is None
            and is_amount_only_row(line.text)
        ):
            opening_balance_amount = parse_pdf_amount(line.text)
            opening_balance_amount_line = line
            continue

        grouped_date_match = parse_grouped_date_line(normalized_line, inferred_year=inferred_year)
        if grouped_date_match is not None:
            current_date, current_section_hint, description_parts, inline_transaction = _parse_grouped_date_line_state(
                line=line,
                grouped_date=grouped_date_match.date,
                grouped_rest=grouped_date_match.rest,
            )
            if inline_transaction is not None:
                if (
                    not opening_balance_inserted
                    and opening_balance_amount is not None
                    and pending_opening_balance_label is not None
                ):
                    opening_row = _build_parsed_transaction(
                        date=grouped_date_match.date,
                        description=pending_opening_balance_label,
                        amount=opening_balance_amount,
                        source_page=(opening_balance_amount_line or pending_opening_balance_line or line).page_number,
                        source_line=(opening_balance_amount_line or pending_opening_balance_line or line).line_number,
                        has_explicit_amount_sign=has_explicit_amount_sign(
                            (opening_balance_amount_line or line).text
                        ),
                    )
                    transactions.append(opening_row)
                    last_transaction_index = len(transactions) - 1
                    last_known_running_balance = opening_balance_amount
                    opening_balance_inserted = True
                transactions.append(inline_transaction)
                last_transaction_index = len(transactions) - 1
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

        pre_amount_description_parts = description_parts
        parsed_row, description_parts, should_continue = _handle_grouped_amount_only_line(
            current_date=current_date,
            description_parts=description_parts,
            line=line,
            section_hint=current_section_hint,
        )
        if should_continue:
            if parsed_row is not None:
                if (
                    not opening_balance_inserted
                    and opening_balance_amount is not None
                    and pending_opening_balance_label is not None
                ):
                    opening_row = _build_parsed_transaction(
                        date=parsed_row.transaction.date,
                        description=pending_opening_balance_label,
                        amount=opening_balance_amount,
                        source_page=(opening_balance_amount_line or pending_opening_balance_line or line).page_number,
                        source_line=(opening_balance_amount_line or pending_opening_balance_line or line).line_number,
                        has_explicit_amount_sign=has_explicit_amount_sign(
                            (opening_balance_amount_line or line).text
                        ),
                    )
                    transactions.append(opening_row)
                    last_transaction_index = len(transactions) - 1
                    last_known_running_balance = opening_balance_amount
                    opening_balance_inserted = True
                transactions.append(parsed_row)
                last_transaction_index = len(transactions) - 1
                if parsed_row.running_balance is not None:
                    last_known_running_balance = parsed_row.running_balance
            elif _is_balance_snapshot_description(pre_amount_description_parts):
                last_known_running_balance = parse_pdf_amount(line.text)
            elif _can_attach_grouped_running_balance(description_parts=description_parts, last_transaction_index=last_transaction_index):
                previous_balance = _resolve_previous_running_balance(transactions, last_transaction_index)
                if previous_balance is None:
                    previous_balance = last_known_running_balance
                transactions[last_transaction_index] = _attach_running_balance_and_reconcile_sign(
                    transaction=transactions[last_transaction_index],
                    running_balance=parse_pdf_amount(line.text),
                    previous_running_balance=previous_balance,
                )
                last_known_running_balance = transactions[last_transaction_index].running_balance
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
    pending_inline: tuple[str, str, int, int] | None = None
    pending_columnar_inline: list[tuple[str, str, int, int]] = []
    columnar_amount_mode = False
    columnar_balance_mode = False
    saw_opening_balance_label = False
    opening_balance_label = "SALDO ANTERIOR"
    columnar_running_balances: list[float] = []

    for line in lines:
        if pending_inline is not None:
            _, _, source_page, _ = pending_inline
            if line.page_number != source_page:
                pending_inline = None

        normalized_line = _normalize_text(line.text)
        if normalized_line.startswith("SALDO ANTERIOR"):
            saw_opening_balance_label = True
            opening_balance_label = "SALDO ANTERIOR"
        elif normalized_line.startswith("SALDO INICIAL"):
            saw_opening_balance_label = True
            opening_balance_label = "SALDO INICIAL"
        if pending_columnar_inline and _is_inline_columnar_amount_header(normalized_line):
            columnar_amount_mode = True
        if _is_inline_columnar_balance_header(normalized_line) and (transactions or pending_columnar_inline):
            columnar_balance_mode = True

        stripped_line = line.text.strip()
        if columnar_balance_mode and is_amount_only_row(stripped_line):
            columnar_running_balances.append(parse_pdf_amount(stripped_line))
            continue
        if columnar_balance_mode and stripped_line and not _is_inline_columnar_balance_header(normalized_line):
            columnar_balance_mode = False

        if columnar_amount_mode and pending_columnar_inline and is_amount_only_row(stripped_line):
            pending_date, pending_description, source_page, source_line = pending_columnar_inline.pop(0)
            amount = parse_pdf_amount(stripped_line)
            signed_amount = compute_hint_signed_amount(raw_amount=amount, description=pending_description)
            transactions.append(
                _build_parsed_transaction(
                    date=pending_date,
                    description=pending_description,
                    amount=signed_amount,
                    source_page=source_page,
                    source_line=source_line,
                )
            )
            candidates += 1
            pending_inline = None
            if not pending_columnar_inline:
                columnar_amount_mode = False
            continue

        if pending_inline is not None and is_amount_only_row(line.text.strip()):
            pending_date, pending_description, source_page, source_line = pending_inline
            amount = parse_pdf_amount(line.text.strip())
            signed_amount = compute_hint_signed_amount(raw_amount=amount, description=pending_description)
            transactions.append(
                _build_parsed_transaction(
                    date=pending_date,
                    description=pending_description,
                    amount=signed_amount,
                    source_page=source_page,
                    source_line=source_line,
                )
            )
            candidates += 1
            pending_inline = None
            continue
        if pending_inline is not None:
            pending_date, pending_description, source_page, source_line = pending_inline
            continuation = line.text.strip()
            if _is_inline_pending_continuation_blocker(continuation):
                pending_inline = None
                pending_columnar_inline = []
                columnar_amount_mode = False
                continue
            if _is_inline_pending_noise_line(continuation):
                continue
            if continuation and not match_inline_row(continuation) and not should_skip_transaction_description(continuation):
                pending_inline = (
                    pending_date,
                    f"{pending_description} {continuation}".strip(),
                    source_page,
                    source_line,
                )
                continue
            pending_inline = None

        parsed_row = _parse_inline_statement_line(line=line, inferred_year=inferred_year)
        if parsed_row is None:
            match = match_inline_row(line.text)
            if match:
                rest = match.group("rest").strip()
                if rest and extract_single_trailing_amount_match(rest) is None and not should_skip_transaction_description(rest):
                    pending_columnar_inline.append(
                        (
                            parse_row_date(match.group("date"), fallback_year=inferred_year),
                            rest,
                            line.page_number,
                            line.line_number,
                        )
                    )
                    pending_inline = (
                        parse_row_date(match.group("date"), fallback_year=inferred_year),
                        rest,
                        line.page_number,
                        line.line_number,
                    )
                    continue
            pending_inline = None
            pending_columnar_inline = []
            columnar_amount_mode = False
            continue
        pending_inline = None
        pending_columnar_inline = []
        columnar_amount_mode = False
        candidates += 1
        transactions.append(parsed_row)

    if saw_opening_balance_label and columnar_running_balances and transactions:
        first_transaction = transactions[0]
        inferred_opening_balance = round(columnar_running_balances[0] - first_transaction.transaction.amount, 2)
        if len(columnar_running_balances) >= 2:
            first_balance = columnar_running_balances[0]
            second_balance = columnar_running_balances[1]
            delta = round(second_balance - first_balance, 2)
            first_amount = round(first_transaction.transaction.amount, 2)
            if abs(delta - first_amount) <= 0.02:
                inferred_opening_balance = round(first_balance, 2)
        opening_transaction = _build_parsed_transaction(
            date=first_transaction.transaction.date,
            description=opening_balance_label,
            amount=inferred_opening_balance,
            source_page=first_transaction.source_page,
            source_line=first_transaction.source_line,
        )
        transactions = [opening_transaction, *transactions]

    return transactions, candidates

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
    normalized_description = _normalize_text(raw_description)
    if normalized_description.endswith(" SALDO"):
        return None, True

    external_reference_id = extract_document_reference(raw_description, layout_profile=tabular_profile)
    amount_details = _build_tabular_amount_details(
        amount_token_value=selected_amount.token.value,
        selected_role=selected_amount.role,
        raw_description=raw_description,
        balance_token_value=selected_amount.balance_token.value if selected_amount.balance_token else None,
    )
    compact_amount = _extract_compact_cd_amount(raw_description)
    if (
        compact_amount is not None
        and amount_details["running_balance"] is None
        and abs(amount_details["signed_amount"]) > 100
        and abs(compact_amount) <= 50
    ):
        amount_details["signed_amount"] = compact_amount
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
        transactions.append(_reconcile_tabular_amount_from_running_balance(parsed_row=parsed_row, transactions=transactions))
    return next_candidates


def _reconcile_tabular_amount_from_running_balance(
    *, parsed_row: _ParsedTransaction, transactions: list[_ParsedTransaction]
) -> _ParsedTransaction:
    if parsed_row.running_balance is None:
        return parsed_row

    previous_running_balance = _resolve_latest_running_balance(transactions)
    if previous_running_balance is None:
        return parsed_row

    delta = round(parsed_row.running_balance - previous_running_balance, 2)
    if abs(delta) < 0.005:
        return parsed_row

    current_amount = parsed_row.transaction.amount
    projected_balance = previous_running_balance + current_amount
    current_error = abs(projected_balance - parsed_row.running_balance)
    if current_error <= 0.02:
        return parsed_row

    amount_looks_like_balance = abs(abs(current_amount) - abs(parsed_row.running_balance)) <= 0.05
    if not amount_looks_like_balance:
        return parsed_row

    normalized_transaction = NormalizedTransaction(
        date=parsed_row.transaction.date,
        description=parsed_row.transaction.description,
        amount=delta,
        type="inflow" if delta >= 0 else "outflow",
    )
    return _ParsedTransaction(
        transaction=normalized_transaction,
        source_page=parsed_row.source_page,
        source_line=parsed_row.source_line,
        running_balance=parsed_row.running_balance,
        external_reference_id=parsed_row.external_reference_id,
        has_explicit_amount_sign=parsed_row.has_explicit_amount_sign,
    )


def _resolve_latest_running_balance(transactions: list[_ParsedTransaction]) -> float | None:
    for item in reversed(transactions):
        if item.running_balance is not None:
            return item.running_balance
    return None


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


def _extract_compact_cd_amount(description: str) -> float | None:
    match = re.search(r"([+\-\u2212]?)\s*(\d{3,5})\s*([CD])\b", description.upper())
    if not match:
        return None
    sign_prefix = match.group(1)
    digits = int(match.group(2))
    suffix = match.group(3)
    value = digits / 100.0
    if sign_prefix in {"-", "\u2212"} or suffix == "D":
        return -abs(value)
    if sign_prefix == "+" or suffix == "C":
        return abs(value)
    return None


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
        return None, [], True
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

    last_part_normalized = _normalize_text(description_parts[-1].strip()) if description_parts else ""
    if last_part_normalized in {"DEBITO", "DÉBITO", "CREDITO", "CRÉDITO"} and len(description_parts) >= 2:
        return None

    description = " ".join(description_parts).strip()
    if should_skip_transaction_description(description):
        return None
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
        has_explicit_amount_sign=has_explicit_amount_sign(line.text),
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
        has_explicit_amount_sign=has_explicit_amount_sign(amount_match.amount_token.value),
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
    has_explicit_amount_sign: bool = False,
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
        has_explicit_amount_sign=has_explicit_amount_sign,
    )


def _can_attach_grouped_running_balance(*, description_parts: list[str], last_transaction_index: int | None) -> bool:
    return bool(not description_parts and last_transaction_index is not None)


def _is_balance_snapshot_description(description_parts: list[str]) -> bool:
    description = " ".join(description_parts).strip()
    if not description:
        return False
    normalized = _normalize_text(description)
    return normalized == "SALDO" or normalized.startswith("SALDO ")


def _resolve_previous_running_balance(transactions: list[_ParsedTransaction], current_index: int) -> float | None:
    for index in range(current_index - 1, -1, -1):
        balance = transactions[index].running_balance
        if balance is not None:
            return balance

    current = transactions[current_index]
    normalized_description = _normalize_text(current.transaction.description)
    if normalized_description.startswith("SALDO ANTERIOR") or normalized_description.startswith("SALDO INICIAL"):
        return current.transaction.amount
    if current_index > 0:
        previous_description = _normalize_text(transactions[current_index - 1].transaction.description)
        if previous_description.startswith("SALDO ANTERIOR") or previous_description.startswith("SALDO INICIAL"):
            return transactions[current_index - 1].transaction.amount
    return None


def _attach_running_balance_and_reconcile_sign(
    *,
    transaction: _ParsedTransaction,
    running_balance: float,
    previous_running_balance: float | None,
) -> _ParsedTransaction:
    signed_amount = transaction.transaction.amount
    if (not transaction.has_explicit_amount_sign) and previous_running_balance is not None:
        delta = running_balance - previous_running_balance
        amount_abs = abs(signed_amount)
        positive_error = abs(delta - amount_abs)
        negative_error = abs(delta + amount_abs)
        if positive_error + 0.005 < negative_error:
            signed_amount = amount_abs
        elif negative_error + 0.005 < positive_error:
            signed_amount = -amount_abs

    normalized_transaction = NormalizedTransaction(
        date=transaction.transaction.date,
        description=transaction.transaction.description,
        amount=signed_amount,
        type="inflow" if signed_amount >= 0 else "outflow",
    )
    return _ParsedTransaction(
        transaction=normalized_transaction,
        source_page=transaction.source_page,
        source_line=transaction.source_line,
        running_balance=running_balance,
        external_reference_id=transaction.external_reference_id,
        has_explicit_amount_sign=transaction.has_explicit_amount_sign,
    )


def _normalize_text(value: str) -> str:
    return normalize_upper_text(value)


def _infer_default_statement_year_from_lines(lines: list[_PdfLine]) -> int | None:
    return infer_default_statement_year(_extract_line_texts(lines))


def _extract_line_texts(lines: list[_PdfLine]) -> list[str]:
    return [line.text for line in lines]


def _is_inline_pending_continuation_blocker(raw_text: str) -> bool:
    normalized = _normalize_text(raw_text)
    if should_ignore_line(normalized):
        return True
    if "DATA" in normalized and "CREDITO" in normalized and "DEBITO" in normalized and "SALDO" in normalized:
        return True
    return "EXTRATO" in normalized and "CONTA" in normalized


def _is_inline_pending_noise_line(raw_text: str) -> bool:
    value = raw_text.strip()
    if not value:
        return True
    return bool(re.fullmatch(r"[|:;,\.\-_/\\Il!]+", value))


def _is_inline_columnar_amount_header(normalized_line: str) -> bool:
    if normalized_line in {"DOC", "VALOR", "SALDO"}:
        return True
    return normalized_line.startswith("DATA:") or normalized_line.startswith("HORA:")


def _is_inline_columnar_balance_header(normalized_line: str) -> bool:
    return normalized_line == "SALDO"

