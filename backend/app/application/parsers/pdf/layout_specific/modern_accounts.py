from __future__ import annotations

import re
from dataclasses import dataclass

from app.application.errors import InvalidFileContentError
from app.application.normalization.date import STATEMENT_DATE_TOKEN
from app.application.normalization.pdf_amount_tokens import (
    AmountToken,
    find_amount_tokens,
    has_explicit_amount_sign,
    parse_pdf_amount,
)
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.pdf_row_match_rules import is_amount_only_row, is_date_only_row
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import (
    build_parsed_transaction,
    normalize_text,
)
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BRADESCO_EMPRESAS_LAYOUT = "bradesco_empresas_negocios_extrato_lancamentos_v1"
SICREDI_MODERN_LAYOUT = "sicredi_extrato_conta_corrente_moderno_movimentacoes_v1"
PICPAY_GROUPED_LAYOUT = "picpay_extrato_conta_grouped_2025_v1"
NEON_MODERN_LAYOUT = "neon_extrato_por_periodo_moderno_v1"
BANCO_BV_GROUPED_LAYOUT = "banco_bv_extrato_periodo_grouped_v1"
BANCO_BV_CURRENT_ACCOUNT_LAYOUT = "banco_bv_extrato_conta_corrente_saldo_dia_v1"

_ALL_LAYOUTS = frozenset(
    {
        BRADESCO_EMPRESAS_LAYOUT,
        SICREDI_MODERN_LAYOUT,
        PICPAY_GROUPED_LAYOUT,
        NEON_MODERN_LAYOUT,
        BANCO_BV_GROUPED_LAYOUT,
        BANCO_BV_CURRENT_ACCOUNT_LAYOUT,
    }
)
_DATE_AT_START_PATTERN = re.compile(
    rf"^\s*(?P<date>{STATEMENT_DATE_TOKEN})(?=\s|$)(?P<rest>.*)$",
    flags=re.IGNORECASE,
)
_DATE_ANY_PATTERN = re.compile(
    rf"(?<!\d)(?P<date>{STATEMENT_DATE_TOKEN})(?!\d)",
    flags=re.IGNORECASE,
)
_TIME_AT_START_PATTERN = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?\s+")
_BRADESCO_DOCUMENT_AND_TYPE_PATTERN = re.compile(
    r"\s+(?P<document>\d{3,})\s+(?:CR[EÉ]DITO|D[EÉ]BITO)\s*$",
    flags=re.IGNORECASE,
)
_SUMMARY_PREFIXES = (
    "SALDO",
    "TOTAL",
    "LIMITE",
    "DISPONIVEL",
    "LANCAMENTOS FUTUROS",
    "MOVIMENTACOES DE",
)
_POSITIVE_DESCRIPTION_HINTS = (
    "CREDITO",
    "PIX RECEBIDO",
    "RECEBIMENTO",
    "DEPOSITO",
    "RESGATE",
    "ESTORNO RECEBIDO",
)
_NEGATIVE_DESCRIPTION_HINTS = (
    "DEBITO",
    "PIX ENVIADO",
    "PAGAMENTO",
    "APLICACAO",
    "TARIFA",
    "ENCARGO",
    "SAIDA",
)


@dataclass(frozen=True, slots=True)
class ModernAccountLayoutParser:
    layout_names: frozenset[str] = _ALL_LAYOUTS

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        _ = context
        if layout_name == BRADESCO_EMPRESAS_LAYOUT:
            rows = _parse_bradesco_rows(lines)
        elif layout_name == SICREDI_MODERN_LAYOUT:
            rows = _parse_sicredi_rows(lines)
        elif layout_name in {PICPAY_GROUPED_LAYOUT, BANCO_BV_GROUPED_LAYOUT}:
            rows = _parse_grouped_long_date_rows(lines)
        elif layout_name == BANCO_BV_CURRENT_ACCOUNT_LAYOUT:
            rows = _parse_bv_current_account_rows(lines)
        elif layout_name == NEON_MODERN_LAYOUT:
            rows = _parse_neon_rows(lines)
        else:
            return None

        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_modern_accounts",
            selection_reason=f"layout_specific_modern_accounts:{layout_name}",
        )


def _parse_bradesco_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    pending_dated_line: tuple[str, str, _PdfLine] | None = None
    for line in lines:
        match = _DATE_AT_START_PATTERN.match(line.text)
        if match is not None:
            pending_dated_line = None
            date = _safe_parse_date(match.group("date"))
            if date is None:
                continue
            rest = match.group("rest")
            amount_tokens = tuple(find_amount_tokens(rest))
            if len(amount_tokens) >= 2:
                row = _build_bradesco_row(
                    date=date,
                    row_text=rest,
                    amount_tokens=amount_tokens,
                    source=line,
                )
                if row is not None:
                    rows.append(row)
            elif not amount_tokens and not _should_skip_description(rest):
                pending_dated_line = (date, rest, line)
            continue

        if not line.text.strip():
            continue

        if pending_dated_line is not None:
            date, dated_text, dated_source = pending_dated_line
            pending_dated_line = None
            row_text = f"{dated_text} {line.text}".strip()
            amount_tokens = tuple(find_amount_tokens(row_text))
            if len(amount_tokens) < 2:
                continue
            row = _build_bradesco_row(
                date=date,
                row_text=row_text,
                amount_tokens=amount_tokens,
                source=dated_source,
            )
            if row is not None:
                rows.append(row)
            continue

        embedded_date_match = _DATE_ANY_PATTERN.search(line.text)
        if embedded_date_match is None or len(embedded_date_match.group("date")) != 10:
            continue
        date = _safe_parse_date(embedded_date_match.group("date"))
        if date is None:
            continue
        row_text = " ".join(
            (
                line.text[: embedded_date_match.start()].strip(),
                line.text[embedded_date_match.end() :].strip(),
            )
        ).strip()
        amount_tokens = tuple(find_amount_tokens(row_text))
        if len(amount_tokens) < 2:
            continue
        raw_description = _remove_amount_tokens(row_text, amount_tokens)
        if _BRADESCO_DOCUMENT_AND_TYPE_PATTERN.search(raw_description) is None:
            continue
        row = _build_bradesco_row(
            date=date,
            row_text=row_text,
            amount_tokens=amount_tokens,
            source=line,
        )
        if row is not None:
            rows.append(row)
    return rows


def _build_bradesco_row(
    *,
    date: str,
    row_text: str,
    amount_tokens: tuple[AmountToken, ...],
    source: _PdfLine,
) -> _ParsedTransaction | None:
    amount_token, balance_token = amount_tokens[-2], amount_tokens[-1]
    raw_description = _remove_amount_tokens(row_text, amount_tokens)
    document_match = _BRADESCO_DOCUMENT_AND_TYPE_PATTERN.search(raw_description)
    external_reference_id = document_match.group("document") if document_match is not None else None
    description = (
        raw_description[: document_match.start()].strip()
        if document_match is not None
        else raw_description
    )
    if _should_skip_description(description):
        return None
    amount, forced_sign = _resolve_signed_amount(
        amount_token,
        description=raw_description,
    )
    return _build_row(
        date=date,
        description=description,
        amount=amount,
        amount_token=amount_token,
        source=source,
        running_balance=parse_pdf_amount(balance_token.value),
        external_reference_id=external_reference_id,
        forced_sign=forced_sign,
    )


def _parse_sicredi_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    inside_movements = False
    rows: list[_ParsedTransaction] = []
    for line in lines:
        normalized = normalize_text(line.text)
        if normalized.startswith("MOVIMENTACOES DE"):
            inside_movements = True
            continue
        if not inside_movements:
            continue
        if normalized.startswith(("CHEQUE ESPECIAL", "FIM DESSE EXTRATO")):
            break
        match = _DATE_AT_START_PATTERN.match(line.text)
        if match is None:
            continue
        amount_tokens = tuple(find_amount_tokens(match.group("rest")))
        if not amount_tokens:
            continue
        date = _safe_parse_date(match.group("date"))
        if date is None:
            continue
        amount_token = amount_tokens[-1]
        description = _remove_amount_tokens(match.group("rest"), amount_tokens)
        if _should_skip_description(description):
            continue
        amount, forced_sign = _resolve_signed_amount(amount_token, description=description)
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                forced_sign=forced_sign,
            )
        )
    return rows


def _parse_grouped_long_date_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    current_date: str | None = None
    rows: list[_ParsedTransaction] = []
    for line in lines:
        normalized = normalize_text(line.text)
        date_match = _DATE_AT_START_PATTERN.match(line.text)
        if date_match is not None and (
            not date_match.group("rest").strip() or "SALDO AO FINAL DO DIA" in normalized
        ):
            current_date = _safe_parse_date(date_match.group("date"))
            continue
        if current_date is None or _TIME_AT_START_PATTERN.match(line.text) is None:
            continue
        without_time = _TIME_AT_START_PATTERN.sub("", line.text, count=1)
        amount_tokens = tuple(find_amount_tokens(without_time))
        if not amount_tokens:
            continue
        amount_token = amount_tokens[-1]
        description = _remove_amount_tokens(without_time, amount_tokens)
        if _should_skip_description(description):
            continue
        amount, forced_sign = _resolve_signed_amount(amount_token, description=description)
        rows.append(
            _build_row(
                date=current_date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                forced_sign=forced_sign,
            )
        )
    return rows


def _parse_bv_current_account_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    pages: dict[int, list[_PdfLine]] = {}
    for line in lines:
        pages.setdefault(line.page_number, []).append(line)

    current_date: str | None = None
    for page_lines in pages.values():
        page_rows, page_date = _parse_bv_columnar_page(
            page_lines,
            previous_date=current_date,
        )
        if page_rows:
            rows.extend(page_rows)
            current_date = page_date or current_date
            continue
        page_rows, current_date = _parse_bv_inline_page(
            page_lines,
            current_date=current_date,
        )
        rows.extend(page_rows)
    return rows


def _parse_bv_columnar_page(
    lines: list[_PdfLine],
    *,
    previous_date: str | None,
) -> tuple[list[_ParsedTransaction], str | None]:
    normalized = [normalize_text(line.text) for line in lines]
    document_start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.fullmatch(r"\d{5,}", line.text.strip())
        ),
        None,
    )
    if document_start is None:
        return [], None

    transaction_dates = [
        parsed
        for line in lines[:document_start]
        if (parsed := _extract_bv_transaction_date(line.text)) is not None
    ]
    if not transaction_dates:
        return [], None

    description_header = next(
        (index for index, value in enumerate(normalized[:document_start]) if value == "LANCAMENTO"),
        None,
    )
    first_date_index = next(
        (
            index
            for index, line in enumerate(lines[:document_start])
            if _extract_bv_transaction_date(line.text) is not None
        ),
        None,
    )
    if first_date_index is None:
        return [], None
    description_start = description_header + 1 if description_header is not None else first_date_index
    descriptions = _group_bv_descriptions(lines[description_start:document_start])
    documents = [
        line.text.strip()
        for line in lines[document_start:]
        if re.fullmatch(r"\d{5,}", line.text.strip())
    ]
    amount_lines = [
        line
        for line in lines[document_start:]
        if is_amount_only_row(line.text)
    ]
    if not descriptions or len(descriptions) != len(amount_lines):
        return [], None

    balances: dict[str, float] = {}
    date_index = 0
    current_date = previous_date
    for (description_line, inline_date), amount_line in zip(descriptions, amount_lines, strict=True):
        description = description_line.text.strip()
        if inline_date is not None:
            current_date = inline_date
        elif normalize_text(description).startswith("SALDO DO DIA"):
            if len(transaction_dates) == 1:
                current_date = transaction_dates[0]
            else:
                if date_index >= len(transaction_dates):
                    return [], None
                current_date = transaction_dates[date_index]
                date_index += 1
        if normalize_text(description).startswith("SALDO DO DIA"):
            if current_date is None:
                return [], None
            balances[current_date] = parse_pdf_amount(amount_line.text.strip())

    rows: list[_ParsedTransaction] = []
    dates_seen: set[str] = set()
    current_date = previous_date
    date_index = 0
    document_index = 0
    for (description_line, inline_date), amount_line in zip(descriptions, amount_lines, strict=True):
        description = description_line.text.strip()
        if inline_date is not None:
            current_date = inline_date
        elif normalize_text(description).startswith("SALDO DO DIA"):
            if len(transaction_dates) == 1:
                current_date = transaction_dates[0]
            else:
                current_date = transaction_dates[date_index]
                date_index += 1
        if normalize_text(description).startswith("SALDO DO DIA") or current_date is None:
            continue
        if _should_skip_description(description):
            continue
        amount_token = AmountToken(
            value=amount_line.text.strip(),
            start=0,
            end=len(amount_line.text.strip()),
        )
        amount, forced_sign = _resolve_signed_amount(amount_token, description=description)
        external_reference_id = documents[document_index] if document_index < len(documents) else None
        document_index += 1
        running_balance = balances.get(current_date) if current_date not in dates_seen else None
        dates_seen.add(current_date)
        rows.append(
            _build_row(
                date=current_date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=description_line,
                running_balance=running_balance,
                external_reference_id=external_reference_id,
                forced_sign=forced_sign,
            )
        )
    return rows, transaction_dates[-1]


def _group_bv_descriptions(
    lines: list[_PdfLine],
) -> list[tuple[_PdfLine, str | None]]:
    grouped: list[tuple[_PdfLine, str | None]] = []
    continuation_prefixes = ("REDE BANCARIA", "CORRESP BANCARIO", "COBRANCA PROPRIA")
    for line in lines:
        value = line.text.strip()
        normalized = normalize_text(value)
        if (
            not value
            or normalized in {"DATA", "LANCAMENTO"}
            or "DOCUMENTO" in normalized
            or is_date_only_row(value)
        ):
            continue
        inline_date = _extract_bv_transaction_date(value)
        if inline_date is not None and "SALDO DO DIA" in normalized:
            date_match = _DATE_AT_START_PATTERN.match(value)
            description = date_match.group("rest").strip() if date_match is not None else value
            grouped.append(
                (
                    _PdfLine(
                        text=description,
                        page_number=line.page_number,
                        line_number=line.line_number,
                    ),
                    inline_date,
                )
            )
            continue
        if normalized.startswith(continuation_prefixes) and grouped:
            previous_line, previous_date = grouped[-1]
            combined = _PdfLine(
                text=f"{previous_line.text.strip()} {value}",
                page_number=previous_line.page_number,
                line_number=previous_line.line_number,
            )
            grouped[-1] = (combined, previous_date)
            continue
        grouped.append((line, None))
    return grouped


def _parse_bv_inline_page(
    lines: list[_PdfLine],
    *,
    current_date: str | None,
) -> tuple[list[_ParsedTransaction], str | None]:
    rows: list[_ParsedTransaction] = []
    pending_description: list[str] = []
    pending_balance: float | None = None
    for line in lines:
        date = _extract_bv_transaction_date(line.text)
        if date is not None:
            current_date = date
        amount_tokens = tuple(find_amount_tokens(line.text))
        normalized = normalize_text(line.text)
        if date is not None and "SALDO DO DIA" in normalized and amount_tokens:
            pending_balance = parse_pdf_amount(amount_tokens[-1].value)
            pending_description.clear()
            continue
        if not amount_tokens:
            if _looks_like_bv_transaction_description(normalized):
                pending_description.append(line.text.strip())
            continue
        if current_date is None:
            continue

        amount_token = amount_tokens[-1]
        raw_description = _remove_amount_tokens(line.text, amount_tokens)
        raw_description = _DATE_AT_START_PATTERN.sub("", raw_description, count=1).strip()
        external_reference_match = re.search(r"\b(\d{5,})\s*$", raw_description)
        external_reference_id = (
            external_reference_match.group(1) if external_reference_match is not None else None
        )
        if external_reference_match is not None:
            raw_description = raw_description[: external_reference_match.start()].strip()
        description = " ".join((*pending_description, raw_description)).strip()
        pending_description.clear()
        if _should_skip_description(description):
            continue
        amount, forced_sign = _resolve_signed_amount(amount_token, description=description)
        rows.append(
            _build_row(
                date=current_date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                running_balance=pending_balance,
                external_reference_id=external_reference_id,
                forced_sign=forced_sign,
            )
        )
        pending_balance = None
    return rows, current_date


def _extract_bv_transaction_date(value: str) -> str | None:
    match = _DATE_AT_START_PATTERN.match(value)
    if match is None or len(match.group("date")) != 10:
        return None
    return _safe_parse_date(match.group("date"))


def _looks_like_bv_transaction_description(normalized: str) -> bool:
    return normalized.startswith(("TARIFA", "RETE", "CORRESP BANCARIO", "CONVEY"))


def _parse_neon_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        date_match = _DATE_ANY_PATTERN.search(line.text)
        if date_match is None:
            continue
        date = _safe_parse_date(date_match.group("date"))
        if date is None:
            continue
        tail = line.text[date_match.end() :]
        tail_without_empty_card = re.sub(r"\s+-\s*$", "", tail)
        amount_tokens = tuple(find_amount_tokens(tail_without_empty_card))
        if not amount_tokens:
            continue
        amount_token = amount_tokens[0]
        balance_token = amount_tokens[1] if len(amount_tokens) > 1 else None
        description = " ".join(line.text[: date_match.start()].strip(" -|:").split())
        if _should_skip_description(description):
            continue
        amount, forced_sign = _resolve_signed_amount(amount_token, description=description)
        rows.append(
            _build_row(
                date=date,
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                running_balance=(
                    parse_pdf_amount(balance_token.value) if balance_token is not None else None
                ),
                forced_sign=forced_sign,
            )
        )
    return rows


def _resolve_signed_amount(amount_token: AmountToken, *, description: str) -> tuple[float, bool]:
    raw_amount = parse_pdf_amount(amount_token.value)
    if has_explicit_amount_sign(amount_token.value):
        return raw_amount, False

    normalized_description = normalize_text(description)
    if any(token in normalized_description for token in _NEGATIVE_DESCRIPTION_HINTS):
        return -abs(raw_amount), True
    if any(token in normalized_description for token in _POSITIVE_DESCRIPTION_HINTS):
        return abs(raw_amount), True
    return raw_amount, False


def _remove_amount_tokens(text: str, amount_tokens: tuple[AmountToken, ...]) -> str:
    value = text
    for token in sorted(amount_tokens, key=lambda item: item.start, reverse=True):
        value = value[: token.start] + " " + value[token.end :]
    value = re.sub(r"(?:^|\s)[+\-]?\s*R\$\s*$", " ", value, flags=re.IGNORECASE)
    return " ".join(value.strip(" -|:").split())


def _should_skip_description(description: str) -> bool:
    normalized = normalize_text(description)
    return not normalized or normalized.startswith(_SUMMARY_PREFIXES)


def _safe_parse_date(raw_date: str) -> str | None:
    try:
        return parse_row_date(raw_date, fallback_year=None)
    except InvalidFileContentError:
        return None


def _build_row(
    *,
    date: str,
    description: str,
    amount: float,
    amount_token: AmountToken,
    source: _PdfLine,
    running_balance: float | None = None,
    external_reference_id: str | None = None,
    forced_sign: bool = False,
) -> _ParsedTransaction:
    return build_parsed_transaction(
        date=date,
        description=description,
        amount=amount,
        source_page=source.page_number,
        source_line=source.line_number,
        running_balance=running_balance,
        external_reference_id=external_reference_id,
        has_explicit_amount_sign=forced_sign or has_explicit_amount_sign(amount_token.value),
    )
