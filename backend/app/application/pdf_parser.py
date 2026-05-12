import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from pypdf import PdfReader

from app.application.errors import InvalidFileContentError
from app.application.layout_profiles.registry import DeclarativeLayoutProfile, get_layout_profile
from app.application.models import CanonicalTransaction, NormalizedTransaction
from app.application.normalization.amount import apply_amount_role_sign, parse_amount
from app.application.normalization.canonical import from_normalized_transaction
from app.application.normalization.text import normalize_upper_text
from app.application.pdf_layout_inference import PdfLayoutInference, infer_pdf_layout
from app.application.pdf_ocr import PDF_OCR_DISABLED_MESSAGE

MONTH_TO_NUMBER = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
}
MONTH_PATTERN = "|".join(MONTH_TO_NUMBER)
DATE_HEADER_PATTERN = re.compile(rf"^(?P<day>\d{{2}})\s+(?P<month>{MONTH_PATTERN})\s+(?P<year>\d{{4}})(?P<rest>.*)$")
SIGN_TOKEN = r"[+\-\u2212]"
AMOUNT_PATTERN = re.compile(rf"^(?:{SIGN_TOKEN}\s*)?(?:R\$\s*)?\d+(?:\.\d{{3}})*,\d{{2}}(?:{SIGN_TOKEN})?$")
AMOUNT_TOKEN_PATTERN = re.compile(
    rf"(?P<amount>(?:{SIGN_TOKEN}\s*)?(?:R\$\s*)?\d+(?:\.\d{{3}})*,\d{{2}}(?:{SIGN_TOKEN})?)"
)
LOOSE_AMOUNT_PATTERN = re.compile(rf"^(?:{SIGN_TOKEN})?(?:\d{{1,3}}(?:\.\d{{3}})+|\d+)(?:[.,]\d{{2}})(?:{SIGN_TOKEN})?$")

INFLOW_HINTS = (
    "TRANSFERENCIA RECEBIDA",
    "RECEBIMENTO",
    "ESTORNO",
    "CREDITO",
    "SALARIO",
)
OUTFLOW_HINTS = (
    "TRANSFERENCIA ENVIADA",
    "PAGAMENTO",
    "COMPRA",
    "DEBITO",
    "SAIDA",
    "TARIFA",
    "SAQUE",
)
IGNORED_LINE_PREFIXES = (
    "SALDO INICIAL",
    "SALDO FINAL",
    "MOVIMENTACOES",
    "EXTRATO GERADO DIA",
    "OUVIDORIA:",
)
IGNORED_LINE_TOKENS = (
    "VALORES EM R",
    "CNPJ AGENCIA CONTA",
)
IGNORED_TRANSACTION_HINTS = (
    "SALDO DO DIA",
    "SALDO FINAL",
    "SALDO INICIAL",
    "LIMITE DA CONTA",
    "TOTAL DE ENTRADAS",
    "TOTAL DE SAIDAS",
    "RESUMO DA FATURA",
    "FATURA ANTERIOR",
    "PAGAMENTO RECEBIDO",
)
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
    parse_metrics: dict[str, int | str]
    canonical_transactions: list[CanonicalTransaction] | None = None


@dataclass(frozen=True)
class _AmountToken:
    value: str
    start: int
    end: int


@dataclass(frozen=True)
class _SelectedTabularAmount:
    token: _AmountToken
    role: str | None
    description_end: int


def parse_pdf_transactions(raw_bytes: bytes) -> PdfParseResult:
    page_texts = _extract_pdf_page_texts(raw_bytes)
    joined_text = "\n".join(page_texts)
    layout = infer_pdf_layout(joined_text)
    layout_profile = get_layout_profile(layout.layout_name)
    lines = _flatten_statement_lines(page_texts)
    grouped_transactions = _parse_grouped_statement_lines(lines)
    transactions = grouped_transactions
    inline_candidates = 0
    inline_transactions_count = 0
    tabular_candidates = 0
    columnar_candidates = 0
    selected_parser = "grouped"
    if not transactions:
        inline_transactions, inline_candidates = _parse_inline_statement_rows(lines)
        inline_transactions_count = len(inline_transactions)
        transactions = inline_transactions
        selected_parser = "inline"
        if not transactions:
            transactions, tabular_candidates = _parse_tabular_statement_rows(lines, layout_profile=layout_profile)
            if transactions:
                selected_parser = "tabular"
        if not transactions:
            transactions, columnar_candidates = _parse_columnar_statement_blocks(lines)
            if transactions:
                selected_parser = "columnar"
        if not transactions:
            selected_parser = "none"
            if inline_candidates > 0 or tabular_candidates > 0 or columnar_candidates > 0:
                raise InvalidFileContentError(
                    "PDF text was extracted, but transactions are in an unsupported table layout."
                )
            raise InvalidFileContentError(
                "PDF text was extracted, but no recognizable transaction row pattern was found."
            )

    canonical_transactions = [
        from_normalized_transaction(
            transaction,
            bank_name=layout_profile.bank if layout_profile is not None else None,
            layout_name=layout.layout_name,
            warnings=["layout_fallback"] if layout.used_fallback else None,
            confidence=layout.confidence,
        )
        for transaction in transactions
    ]

    return PdfParseResult(
        transactions=transactions,
        canonical_transactions=canonical_transactions,
        layout=layout,
        extracted_text=joined_text,
        parse_metrics={
            "page_count": len(page_texts),
            "extracted_char_count": len(joined_text),
            "flattened_line_count": len(lines),
            "grouped_transactions_count": len(grouped_transactions),
            "inline_candidates_count": inline_candidates,
            "inline_transactions_count": inline_transactions_count,
            "selected_parser": selected_parser,
        },
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


def _flatten_statement_lines(page_texts: list[str]) -> list[str]:
    lines: list[str] = []
    for page_text in page_texts:
        for line in page_text.splitlines():
            cleaned = " ".join(line.split())
            if cleaned:
                lines.append(cleaned)
    return lines


def _parse_grouped_statement_lines(lines: list[str]) -> list[NormalizedTransaction]:
    transactions: list[NormalizedTransaction] = []
    current_date: str | None = None
    current_section_hint: str | None = None
    description_parts: list[str] = []
    inferred_year = _infer_default_statement_year(lines)

    for line in lines:
        normalized_line = _normalize_text(line)
        date_match = DATE_HEADER_PATTERN.match(normalized_line)
        if date_match:
            current_date = _build_iso_date(
                year=date_match.group("year"),
                month_abbrev=date_match.group("month"),
                day=date_match.group("day"),
            )
            current_section_hint = None
            description_parts = []
            rest = date_match.group("rest")
            maybe_hint = _section_hint(rest)
            if maybe_hint:
                current_section_hint = maybe_hint
            inline_transaction = _build_inline_transaction_from_date_rest(
                date=current_date,
                rest=rest,
                section_hint=current_section_hint,
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
            current_date = _build_iso_date(
                year=year_value,
                month_abbrev=month_only_match.group("month"),
                day=month_only_match.group("day"),
            )
            current_section_hint = None
            description_parts = []
            rest = month_only_match.group("rest")
            maybe_hint = _section_hint(rest)
            if maybe_hint:
                current_section_hint = maybe_hint
            inline_transaction = _build_inline_transaction_from_date_rest(
                date=current_date,
                rest=rest,
                section_hint=current_section_hint,
            )
            if inline_transaction is not None:
                transactions.append(inline_transaction)
                description_parts = []
            continue

        if current_date is None:
            continue

        maybe_hint = _section_hint(normalized_line)
        if maybe_hint:
            current_section_hint = maybe_hint
            description_parts = []
            continue

        if _should_ignore_line(normalized_line):
            description_parts = []
            continue

        if AMOUNT_PATTERN.fullmatch(line):
            if not description_parts:
                continue
            amount = _parse_pdf_amount(line)
            description = " ".join(description_parts).strip()
            signed_amount = _apply_sign_hints(amount=amount, description=description, section_hint=current_section_hint)
            transactions.append(
                NormalizedTransaction(
                    date=current_date,
                    description=description,
                    amount=signed_amount,
                    type="inflow" if signed_amount >= 0 else "outflow",
                )
            )
            description_parts = []
            continue

        description_parts.append(line.strip())

    return transactions


def _parse_inline_statement_rows(lines: list[str]) -> tuple[list[NormalizedTransaction], int]:
    transactions: list[NormalizedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year(lines)

    for line in lines:
        match = INLINE_ROW_PATTERN.match(line)
        if not match:
            continue

        rest = match.group("rest").strip()
        amount_tokens = _find_amount_tokens(rest)
        if len(amount_tokens) != 1:
            continue
        amount_token = amount_tokens[0]
        if rest[amount_token.end :].strip():
            continue

        raw_description = rest[: amount_token.start].strip()
        if not raw_description or _should_skip_transaction_description(raw_description):
            continue

        candidates += 1

        amount = _parse_pdf_amount(amount_token.value)
        signed_amount = _apply_sign_hints(
            amount=amount,
            description=raw_description,
            section_hint=None,
        )
        transactions.append(
            NormalizedTransaction(
                date=_parse_statement_date(match.group("date"), fallback_year=inferred_year),
                description=raw_description,
                amount=signed_amount,
                type="inflow" if signed_amount >= 0 else "outflow",
            )
        )

    return transactions, candidates


def _parse_tabular_statement_rows(
    lines: list[str], *, layout_profile: DeclarativeLayoutProfile | None = None
) -> tuple[list[NormalizedTransaction], int]:
    transactions: list[NormalizedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year(lines)
    tabular_profile = layout_profile if _has_declarative_table_header(lines, layout_profile) else None

    for line in lines:
        match = TABULAR_DATE_PREFIX_PATTERN.match(line)
        if not match:
            continue

        rest = match.group("rest").strip()
        if not rest:
            continue

        amount_tokens = _find_amount_tokens(rest)
        if not amount_tokens:
            continue
        candidates += 1

        selected_amount = _select_tabular_amount_token(amount_tokens, layout_profile=tabular_profile)
        if selected_amount is None:
            continue

        raw_description = rest[: selected_amount.description_end].strip()
        if not raw_description or _should_skip_transaction_description(raw_description):
            continue

        amount = apply_amount_role_sign(_parse_pdf_amount(selected_amount.token.value), selected_amount.role)
        if selected_amount.role in {"credit", "debit"}:
            signed_amount = amount
        else:
            signed_amount = _apply_sign_hints(
                amount=amount,
                description=raw_description,
                section_hint=None,
            )
        transactions.append(
            NormalizedTransaction(
                date=_parse_statement_date(match.group("date"), fallback_year=inferred_year),
                description=raw_description,
                amount=signed_amount,
                type="inflow" if signed_amount >= 0 else "outflow",
            )
        )

    return transactions, candidates


def _select_tabular_amount_token(
    tokens: list[_AmountToken], *, layout_profile: DeclarativeLayoutProfile | None = None
) -> _SelectedTabularAmount | None:
    if not tokens:
        return None
    declarative_selection = _select_declarative_tabular_amount(tokens, layout_profile)
    if declarative_selection is not None:
        return declarative_selection
    if len(tokens) == 1:
        return _SelectedTabularAmount(token=tokens[0], role=None, description_end=tokens[0].start)
    # In statement-like tables with balance column, the rightmost amount is usually balance.
    return _SelectedTabularAmount(token=tokens[-2], role=None, description_end=tokens[-2].start)


def _select_declarative_tabular_amount(
    tokens: list[_AmountToken], layout_profile: DeclarativeLayoutProfile | None
) -> _SelectedTabularAmount | None:
    if layout_profile is None:
        return None

    amount_roles = tuple(role for role in layout_profile.expected_column_order if role in {"amount", "credit", "debit", "balance"})
    if not amount_roles:
        return None

    aligned_tokens = tokens[-len(amount_roles) :]
    aligned_roles = amount_roles[-len(aligned_tokens) :]
    role_tokens = list(zip(aligned_roles, aligned_tokens, strict=False))
    transaction_role_tokens = [(role, token) for role, token in role_tokens if role != "balance"]
    if not transaction_role_tokens:
        return None

    description_end = tokens[0].start if {"credit", "debit"} & set(amount_roles) else transaction_role_tokens[0][1].start
    for preferred_role in ("debit", "credit", "amount"):
        for role, token in transaction_role_tokens:
            if role != preferred_role:
                continue
            amount = _parse_pdf_amount(token.value)
            if preferred_role in {"credit", "debit"} and abs(amount) < 0.000001:
                continue
            return _SelectedTabularAmount(token=token, role=role, description_end=description_end)

    role, token = transaction_role_tokens[0]
    return _SelectedTabularAmount(token=token, role=role, description_end=description_end)


def _has_declarative_table_header(lines: list[str], layout_profile: DeclarativeLayoutProfile | None) -> bool:
    if layout_profile is None or not layout_profile.expected_column_order or not layout_profile.column_aliases:
        return False

    required_roles = tuple(role for role in layout_profile.expected_column_order if role not in {"document", "balance"})
    if not required_roles:
        return False

    minimum_matches = min(3, len(required_roles))
    for line in lines:
        normalized_line = _normalize_text(line)
        matches = 0
        for role in required_roles:
            aliases = layout_profile.column_aliases.get(role, ())
            if any(_normalize_text(alias) in normalized_line for alias in aliases):
                matches += 1
        if matches >= minimum_matches:
            return True

    return False


def _find_amount_tokens(text: str) -> list[_AmountToken]:
    return [
        _AmountToken(value=match.group("amount"), start=match.start("amount"), end=match.end("amount"))
        for match in AMOUNT_TOKEN_PATTERN.finditer(text)
    ]


def _parse_pdf_amount(raw: str) -> float:
    return parse_amount(raw)


def _parse_columnar_statement_blocks(lines: list[str]) -> tuple[list[NormalizedTransaction], int]:
    transactions: list[NormalizedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year(lines)
    index = 0

    while index < len(lines):
        raw_date = lines[index].strip()
        if not DATE_ONLY_PATTERN.fullmatch(raw_date):
            index += 1
            continue

        if index + 3 >= len(lines):
            index += 1
            continue

        description = lines[index + 1].strip()
        type_raw = lines[index + 2].strip()
        amount_raw = lines[index + 3].strip()
        if not description or _is_columnar_header_line(description):
            index += 1
            continue
        if not _is_transaction_type_hint(type_raw):
            index += 1
            continue
        if not _is_amount_like(amount_raw):
            index += 1
            continue

        candidates += 1
        amount = _apply_type_sign_hint(_parse_pdf_amount(amount_raw), type_raw)
        signed_amount = _apply_sign_hints(amount=amount, description=description, section_hint=None)
        transactions.append(
            NormalizedTransaction(
                date=_parse_statement_date(raw_date, fallback_year=inferred_year),
                description=description,
                amount=signed_amount,
                type="inflow" if signed_amount >= 0 else "outflow",
            )
        )

        next_index = index + 4
        if next_index < len(lines) and _is_amount_like(lines[next_index].strip()):
            next_index += 1
        index = next_index

    return transactions, candidates


def _is_amount_like(raw: str) -> bool:
    value = raw.replace("\u2212", "-")
    value = re.sub(r"(?i)R\$", "", value).strip()
    return bool(LOOSE_AMOUNT_PATTERN.fullmatch(value))


def _build_inline_transaction_from_date_rest(
    *,
    date: str,
    rest: str,
    section_hint: str | None,
) -> NormalizedTransaction | None:
    text = rest.strip()
    if not text:
        return None
    amount_tokens = _find_amount_tokens(text)
    if len(amount_tokens) != 1:
        return None
    amount_token = amount_tokens[0]
    if text[amount_token.end :].strip():
        return None
    raw_description = text[: amount_token.start].strip()
    if not raw_description or _should_skip_transaction_description(raw_description):
        return None
    amount = _parse_pdf_amount(amount_token.value)
    signed_amount = _apply_sign_hints(
        amount=amount,
        description=raw_description,
        section_hint=section_hint,
    )
    return NormalizedTransaction(
        date=date,
        description=raw_description,
        amount=signed_amount,
        type="inflow" if signed_amount >= 0 else "outflow",
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


def _infer_default_statement_year(lines: list[str]) -> int | None:
    year_counts: dict[int, int] = {}

    for line in lines:
        for raw in re.findall(r"\b\d{2}/\d{2}/(\d{4})\b", line):
            year = int(raw)
            year_counts[year] = year_counts.get(year, 0) + 1
        normalized_line = _normalize_text(line)
        for raw in re.findall(rf"\b\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+(\d{{4}})\b", normalized_line):
            year = int(raw)
            year_counts[year] = year_counts.get(year, 0) + 1

    if not year_counts:
        return None
    return max(year_counts.items(), key=lambda item: item[1])[0]


def _parse_statement_date(raw: str, fallback_year: int | None) -> str:
    value = raw.strip()
    upper_value = _normalize_text(value)
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
        return _parse_slash_date(value)

    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2}", value):
        day, month, year = value.split("/")
        return _parse_slash_date(f"{day}/{month}/20{year}")

    if re.fullmatch(r"\d{1,2}/\d{1,2}", value):
        if fallback_year is None:
            fallback_year = datetime.utcnow().year
        return _parse_slash_date(f"{value}/{fallback_year}")

    month_match = re.fullmatch(rf"(?P<day>\d{{1,2}})\s+(?P<month>{MONTH_PATTERN})(?:\s+(?P<year>\d{{4}}))?", upper_value)
    if month_match:
        day = int(month_match.group("day"))
        month_abbrev = month_match.group("month")
        month_value = MONTH_TO_NUMBER.get(month_abbrev)
        if month_value is None:
            raise InvalidFileContentError(f"Invalid month value in PDF statement: {month_abbrev!r}.")
        year_raw = month_match.group("year")
        if year_raw:
            year_value = int(year_raw)
        else:
            year_value = fallback_year if fallback_year is not None else datetime.utcnow().year
        try:
            return datetime(year_value, month_value, day).strftime("%Y-%m-%d")
        except ValueError as exc:
            raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.") from exc

    raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.")


def _section_hint(text: str) -> str | None:
    normalized = _normalize_text(text)
    if "TOTAL DE ENTRADAS" in normalized:
        return "inflow"
    if "TOTAL DE SAIDAS" in normalized:
        return "outflow"
    return None


def _should_ignore_line(normalized_line: str) -> bool:
    if not normalized_line:
        return True
    if normalized_line in {"-", "--"}:
        return True
    if re.fullmatch(r"\d+\s+DE\s+\d+", normalized_line):
        return True
    if any(normalized_line.startswith(prefix) for prefix in IGNORED_LINE_PREFIXES):
        return True
    if any(token in normalized_line for token in IGNORED_LINE_TOKENS):
        return True
    return False


def _should_skip_transaction_description(description: str) -> bool:
    normalized_description = _normalize_text(description)
    if not normalized_description:
        return True
    if any(hint in normalized_description for hint in IGNORED_TRANSACTION_HINTS):
        return True
    if normalized_description.startswith("SALDO "):
        return True
    return False


def _apply_sign_hints(amount: float, description: str, section_hint: str | None) -> float:
    normalized_description = _normalize_text(description)
    if any(token in normalized_description for token in INFLOW_HINTS):
        return abs(amount)
    if any(token in normalized_description for token in OUTFLOW_HINTS):
        return -abs(amount)
    if section_hint == "inflow":
        return abs(amount)
    if section_hint == "outflow":
        return -abs(amount)
    return amount


def _parse_slash_date(raw: str) -> str:
    try:
        return datetime.strptime(raw, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid date value in PDF statement: {raw!r}.") from exc


def _build_iso_date(year: str, month_abbrev: str, day: str) -> str:
    month_value = MONTH_TO_NUMBER.get(month_abbrev)
    if month_value is None:
        raise InvalidFileContentError(f"Invalid month value in PDF statement: {month_abbrev!r}.")
    try:
        return datetime(int(year), month_value, int(day)).strftime("%Y-%m-%d")
    except ValueError as exc:
        raise InvalidFileContentError(f"Invalid date value in PDF statement: {day}/{month_abbrev}/{year}.") from exc


def _normalize_text(value: str) -> str:
    return normalize_upper_text(value)
