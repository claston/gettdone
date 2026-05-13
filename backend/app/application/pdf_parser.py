import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader

from app.application.errors import InvalidFileContentError
from app.application.layout_profiles.registry import DeclarativeLayoutProfile, get_layout_profile
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.normalization.amount import apply_amount_role_sign
from app.application.normalization.balance import annotate_balance_consistency
from app.application.normalization.canonical import build_canonical_transactions
from app.application.normalization.canonical_metrics import build_canonical_quality_metrics
from app.application.normalization.date import MONTH_PATTERN, build_iso_date, infer_default_statement_year, parse_statement_date
from app.application.normalization.pdf_amount_tokens import AmountToken, find_amount_tokens, is_amount_like, parse_pdf_amount
from app.application.normalization.pdf_parse_metrics import build_pdf_parse_metrics
from app.application.normalization.pdf_parser_selection import select_parsed_rows
from app.application.normalization.pdf_text_rules import (
    apply_sign_hints,
    section_hint,
    should_ignore_line,
    should_skip_transaction_description,
)
from app.application.normalization.text import normalize_upper_text
from app.application.pdf_layout_inference import PdfLayoutInference, infer_pdf_layout
from app.application.pdf_ocr import PDF_OCR_DISABLED_MESSAGE

DATE_HEADER_PATTERN = re.compile(rf"^(?P<day>\d{{2}})\s+(?P<month>{MONTH_PATTERN})\s+(?P<year>\d{{4}})(?P<rest>.*)$")
SIGN_TOKEN = r"[+\-\u2212]"
AMOUNT_PATTERN = re.compile(rf"^(?:{SIGN_TOKEN}\s*)?(?:R\$\s*)?\d+(?:\.\d{{3}})*,\d{{2}}(?:{SIGN_TOKEN})?$")

DEBIT_TYPE_HINTS = ("DEBITO", "DEBIT", "DEB")
CREDIT_TYPE_HINTS = ("CREDITO", "CREDIT", "CRED")
COLUMNAR_HEADER_TOKENS = {
    "DATA",
    "DESCRICAO",
    "TIPO",
    "VALOR",
    "VALOR (R$)",
    "SALDO",
    "SALDO (R$)",
}
DATE_SLASH_TOKEN = r"\d{1,2}/\d{1,2}(?:/\d{2,4})?"
DATE_MONTH_TOKEN = rf"\d{{1,2}}\s+(?:{MONTH_PATTERN})(?:\s+\d{{4}})?"
DATE_TOKEN = rf"(?:{DATE_SLASH_TOKEN}|{DATE_MONTH_TOKEN})"
INLINE_ROW_PATTERN = re.compile(rf"^(?P<date>{DATE_TOKEN})\s+(?P<rest>.+)$", re.IGNORECASE)
TABULAR_DATE_PREFIX_PATTERN = re.compile(rf"^(?P<date>{DATE_TOKEN})\s+(?P<rest>.+)$", re.IGNORECASE)
DATE_ONLY_PATTERN = re.compile(rf"^{DATE_TOKEN}$", re.IGNORECASE)


@dataclass(frozen=True)
class PdfParseResult:
    transactions: list[NormalizedTransaction]
    layout: PdfLayoutInference
    extracted_text: str
    parse_metrics: dict[str, int | float | str]
    canonical_transactions: list[CanonicalTransaction] | None = None


@dataclass(frozen=True)
class _SelectedTabularAmount:
    token: AmountToken
    role: str | None
    description_end: int
    balance_token: AmountToken | None = None


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
    transactions = [item.transaction for item in parsed_rows]
    inline_candidates = selection.inline_candidates
    inline_transactions_count = selection.inline_transactions_count
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
            page_count=len(page_texts),
            extracted_char_count=len(joined_text),
            flattened_line_count=len(lines),
            grouped_transactions_count=len(grouped_rows),
            inline_candidates_count=inline_candidates,
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
    inferred_year = infer_default_statement_year([line.text for line in lines])

    for line in lines:
        normalized_line = _normalize_text(line.text)
        date_match = DATE_HEADER_PATTERN.match(normalized_line)
        if date_match:
            current_date = build_iso_date(
                year=date_match.group("year"),
                month_abbrev=date_match.group("month"),
                day=date_match.group("day"),
            )
            current_section_hint = None
            description_parts = []
            rest = date_match.group("rest")
            maybe_hint = section_hint(rest)
            if maybe_hint:
                current_section_hint = maybe_hint
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

        month_only_match = re.fullmatch(
            rf"(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_PATTERN})(?:\s+(?P<year>\d{{4}}))?(?P<rest>.*)",
            normalized_line,
        )
        if month_only_match:
            year_value = month_only_match.group("year")
            if year_value is None:
                year_value = str(inferred_year if inferred_year is not None else datetime.utcnow().year)
            current_date = build_iso_date(
                year=year_value,
                month_abbrev=month_only_match.group("month"),
                day=month_only_match.group("day"),
            )
            current_section_hint = None
            description_parts = []
            rest = month_only_match.group("rest")
            maybe_hint = section_hint(rest)
            if maybe_hint:
                current_section_hint = maybe_hint
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

        maybe_hint = section_hint(normalized_line)
        if maybe_hint:
            current_section_hint = maybe_hint
            description_parts = []
            continue

        if should_ignore_line(normalized_line) or re.fullmatch(r"\d+\s+DE\s+\d+", normalized_line):
            description_parts = []
            continue

        if AMOUNT_PATTERN.fullmatch(line.text):
            if not description_parts:
                continue
            amount = parse_pdf_amount(line.text)
            description = " ".join(description_parts).strip()
            signed_amount = apply_sign_hints(
                amount=amount,
                description=description,
                section_hint_value=current_section_hint,
            )
            transactions.append(
                _ParsedTransaction(
                    transaction=NormalizedTransaction(
                        date=current_date,
                        description=description,
                        amount=signed_amount,
                        type="inflow" if signed_amount >= 0 else "outflow",
                    ),
                    source_page=line.page_number,
                    source_line=line.line_number,
                )
            )
            description_parts = []
            continue

        description_parts.append(line.text.strip())

    return transactions


def _parse_inline_statement_rows(lines: list[_PdfLine]) -> tuple[list[_ParsedTransaction], int]:
    transactions: list[_ParsedTransaction] = []
    candidates = 0
    inferred_year = infer_default_statement_year([line.text for line in lines])

    for line in lines:
        match = INLINE_ROW_PATTERN.match(line.text)
        if not match:
            continue

        rest = match.group("rest").strip()
        amount_tokens = find_amount_tokens(rest)
        if len(amount_tokens) != 1:
            continue
        amount_token = amount_tokens[0]
        if rest[amount_token.end :].strip():
            continue

        raw_description = rest[: amount_token.start].strip()
        if not raw_description or should_skip_transaction_description(raw_description):
            continue

        candidates += 1

        amount = parse_pdf_amount(amount_token.value)
        signed_amount = apply_sign_hints(
            amount=amount,
            description=raw_description,
            section_hint_value=None,
        )
        transactions.append(
            _ParsedTransaction(
                transaction=NormalizedTransaction(
                    date=parse_statement_date(match.group("date"), fallback_year=inferred_year),
                    description=raw_description,
                    amount=signed_amount,
                    type="inflow" if signed_amount >= 0 else "outflow",
                ),
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
    inferred_year = infer_default_statement_year([line.text for line in lines])
    tabular_profile = layout_profile if _has_declarative_table_header(lines, layout_profile) else None

    for line in lines:
        match = TABULAR_DATE_PREFIX_PATTERN.match(line.text)
        if not match:
            continue

        rest = match.group("rest").strip()
        if not rest:
            continue

        amount_tokens = find_amount_tokens(rest)
        if not amount_tokens:
            continue
        candidates += 1

        selected_amount = _select_tabular_amount_token(amount_tokens, layout_profile=tabular_profile)
        if selected_amount is None:
            continue

        raw_description = rest[: selected_amount.description_end].strip()
        if not raw_description or should_skip_transaction_description(raw_description):
            continue

        external_reference_id = _extract_document_reference(raw_description, layout_profile=tabular_profile)

        running_balance: float | None = None
        if selected_amount.balance_token is not None:
            running_balance = parse_pdf_amount(selected_amount.balance_token.value)

        amount = apply_amount_role_sign(parse_pdf_amount(selected_amount.token.value), selected_amount.role)
        if selected_amount.role in {"credit", "debit"}:
            signed_amount = amount
        else:
            signed_amount = apply_sign_hints(
                amount=amount,
                description=raw_description,
                section_hint_value=None,
            )
        transactions.append(
            _ParsedTransaction(
                transaction=NormalizedTransaction(
                    date=parse_statement_date(match.group("date"), fallback_year=inferred_year),
                    description=raw_description,
                    amount=signed_amount,
                    type="inflow" if signed_amount >= 0 else "outflow",
                ),
                source_page=line.page_number,
                source_line=line.line_number,
                running_balance=running_balance,
                external_reference_id=external_reference_id,
            )
        )

    return transactions, candidates


def _select_tabular_amount_token(
    tokens: list[AmountToken], *, layout_profile: DeclarativeLayoutProfile | None = None
) -> _SelectedTabularAmount | None:
    if not tokens:
        return None
    declarative_selection = _select_declarative_tabular_amount(tokens, layout_profile)
    if declarative_selection is not None:
        return declarative_selection
    if len(tokens) == 1:
        return _SelectedTabularAmount(token=tokens[0], role=None, description_end=tokens[0].start, balance_token=None)
    # In statement-like tables with balance column, the rightmost amount is usually balance.
    return _SelectedTabularAmount(token=tokens[-2], role=None, description_end=tokens[-2].start, balance_token=tokens[-1])


def _select_declarative_tabular_amount(
    tokens: list[AmountToken], layout_profile: DeclarativeLayoutProfile | None
) -> _SelectedTabularAmount | None:
    if layout_profile is None:
        return None

    amount_roles = tuple(role for role in layout_profile.expected_column_order if role in {"amount", "credit", "debit", "balance"})
    if not amount_roles:
        return None

    aligned_tokens = tokens[-len(amount_roles) :]
    aligned_roles = amount_roles[-len(aligned_tokens) :]
    role_tokens = list(zip(aligned_roles, aligned_tokens, strict=False))
    role_token_map = {role: token for role, token in role_tokens}
    transaction_role_tokens = [(role, token) for role, token in role_tokens if role != "balance"]
    if not transaction_role_tokens:
        return None

    description_end = tokens[0].start if {"credit", "debit"} & set(amount_roles) else transaction_role_tokens[0][1].start
    balance_token = role_token_map.get("balance")
    for preferred_role in ("debit", "credit", "amount"):
        for role, token in transaction_role_tokens:
            if role != preferred_role:
                continue
            amount = parse_pdf_amount(token.value)
            if preferred_role in {"credit", "debit"} and abs(amount) < 0.000001:
                continue
            return _SelectedTabularAmount(
                token=token,
                role=role,
                description_end=description_end,
                balance_token=balance_token,
            )

    role, token = transaction_role_tokens[0]
    return _SelectedTabularAmount(token=token, role=role, description_end=description_end, balance_token=balance_token)


def _extract_document_reference(raw_description: str, *, layout_profile: DeclarativeLayoutProfile | None) -> str | None:
    if layout_profile is None:
        return None
    if "document" not in set(layout_profile.expected_column_order):
        return None
    parts = raw_description.split()
    if not parts:
        return None
    candidate = parts[-1].strip()
    if re.fullmatch(r"[A-Za-z0-9./_-]{3,}", candidate):
        return candidate
    return None


def _has_declarative_table_header(lines: list[_PdfLine], layout_profile: DeclarativeLayoutProfile | None) -> bool:
    if layout_profile is None or not layout_profile.expected_column_order or not layout_profile.column_aliases:
        return False

    required_roles = tuple(role for role in layout_profile.expected_column_order if role not in {"document", "balance"})
    if not required_roles:
        return False

    minimum_matches = min(3, len(required_roles))
    for line in lines:
        normalized_line = _normalize_text(line.text)
        matches = 0
        for role in required_roles:
            aliases = layout_profile.column_aliases.get(role, ())
            if any(_normalize_text(alias) in normalized_line for alias in aliases):
                matches += 1
        if matches >= minimum_matches:
            return True

    return False


def _parse_columnar_statement_blocks(lines: list[_PdfLine]) -> tuple[list[_ParsedTransaction], int]:
    transactions: list[_ParsedTransaction] = []
    candidates = 0
    inferred_year = infer_default_statement_year([line.text for line in lines])
    index = 0

    while index < len(lines):
        raw_date = lines[index].text.strip()
        if not DATE_ONLY_PATTERN.fullmatch(raw_date):
            index += 1
            continue

        if index + 3 >= len(lines):
            index += 1
            continue

        description = lines[index + 1].text.strip()
        type_raw = lines[index + 2].text.strip()
        amount_raw = lines[index + 3].text.strip()
        if not description or _is_columnar_header_line(description):
            index += 1
            continue
        if not _is_transaction_type_hint(type_raw):
            index += 1
            continue
        if not is_amount_like(amount_raw):
            index += 1
            continue

        candidates += 1
        amount = _apply_type_sign_hint(parse_pdf_amount(amount_raw), type_raw)
        signed_amount = apply_sign_hints(amount=amount, description=description, section_hint_value=None)
        transactions.append(
            _ParsedTransaction(
                transaction=NormalizedTransaction(
                    date=parse_statement_date(raw_date, fallback_year=inferred_year),
                    description=description,
                    amount=signed_amount,
                    type="inflow" if signed_amount >= 0 else "outflow",
                ),
                source_page=lines[index].page_number,
                source_line=lines[index].line_number,
            )
        )

        next_index = index + 4
        if next_index < len(lines) and is_amount_like(lines[next_index].text.strip()):
            next_index += 1
        index = next_index

    return transactions, candidates


def _build_inline_transaction_from_date_rest(
    *,
    date: str,
    rest: str,
    section_hint: str | None,
    source_page: int | None = None,
    source_line: int | None = None,
) -> _ParsedTransaction | None:
    text = rest.strip()
    if not text:
        return None
    amount_tokens = find_amount_tokens(text)
    if len(amount_tokens) != 1:
        return None
    amount_token = amount_tokens[0]
    if text[amount_token.end :].strip():
        return None
    raw_description = text[: amount_token.start].strip()
    if not raw_description or should_skip_transaction_description(raw_description):
        return None
    amount = parse_pdf_amount(amount_token.value)
    signed_amount = apply_sign_hints(
        amount=amount,
        description=raw_description,
        section_hint_value=section_hint,
    )
    return _ParsedTransaction(
        transaction=NormalizedTransaction(
            date=date,
            description=raw_description,
            amount=signed_amount,
            type="inflow" if signed_amount >= 0 else "outflow",
        ),
        source_page=source_page,
        source_line=source_line,
    )


def _is_transaction_type_hint(raw: str) -> bool:
    normalized = _normalize_text(raw)
    if not normalized:
        return False
    if any(token == normalized or normalized.startswith(token + " ") for token in DEBIT_TYPE_HINTS):
        return True
    if any(token == normalized or normalized.startswith(token + " ") for token in CREDIT_TYPE_HINTS):
        return True
    return False


def _apply_type_sign_hint(amount: float, type_raw: str) -> float:
    normalized = _normalize_text(type_raw)
    if any(token == normalized or normalized.startswith(token + " ") for token in DEBIT_TYPE_HINTS):
        return -abs(amount)
    if any(token == normalized or normalized.startswith(token + " ") for token in CREDIT_TYPE_HINTS):
        return abs(amount)
    return amount


def _is_columnar_header_line(raw: str) -> bool:
    return _normalize_text(raw) in COLUMNAR_HEADER_TOKENS


def _normalize_text(value: str) -> str:
    return normalize_upper_text(value)
