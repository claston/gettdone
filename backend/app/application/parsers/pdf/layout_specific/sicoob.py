from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.application.normalization.pdf_amount_tokens import (
    AmountToken,
    find_amount_tokens,
    has_explicit_amount_sign,
    parse_pdf_amount,
)
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import (
    build_parsed_transaction,
    infer_default_statement_year_from_lines,
    normalize_text,
)
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

SICOOB_CREDIT_CARD_LAYOUT = "sicoob_fatura_cartao_credito_movimentos_v1"
SICOOB_HISTORY_LAYOUT = "sicoob_sisbr_extrato_conta_corrente_historico_movimentacao_v1"
SICOOB_MONOSPACE_LAYOUT = "sicoob_sisbr_extrato_conta_corrente_monospace_valor_dc_v1"
SICOOB_FUTURE_CARD_LAYOUT = "sicoob_cartao_lancamentos_futuros_v1"
SICOOB_APPLICATIONS_LAYOUT = "sicoob_extrato_aplicacoes_v1"
SICOOB_PIX_LIST_LAYOUT = "sicoob_extrato_pix_lista_v1"
SICOOB_MODERN_CURRENT_LAYOUT = "sicoob_extrato_conta_corrente_moderno_v1"
SICOOB_BOLETO_RECEIPT_LAYOUT = "sicoob_comprovante_pagamento_boleto_v1"

_ALL_LAYOUTS = frozenset(
    {
        SICOOB_CREDIT_CARD_LAYOUT,
        SICOOB_HISTORY_LAYOUT,
        SICOOB_MONOSPACE_LAYOUT,
        SICOOB_FUTURE_CARD_LAYOUT,
        SICOOB_APPLICATIONS_LAYOUT,
        SICOOB_PIX_LIST_LAYOUT,
        SICOOB_MODERN_CURRENT_LAYOUT,
        SICOOB_BOLETO_RECEIPT_LAYOUT,
    }
)
_DATE_ROW_PATTERN = re.compile(
    r"^\s*(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+(?P<rest>.+)$",
    flags=re.IGNORECASE,
)
_DATE_ONLY_PATTERN = re.compile(
    r"^\s*(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*$",
    flags=re.IGNORECASE,
)
_PIX_ROW_PATTERN = re.compile(
    r"^\s*(?P<month>JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)\s+"
    r"(?P<day>\d{1,2})\s+(?P<rest>.+)$",
    flags=re.IGNORECASE,
)
_OPENING_OR_BALANCE_TOKENS = (
    "SALDO ANTERIOR",
    "SALDO BLOQ",
    "SALDO BLOQUEADO",
    "SALDO DO DIA",
    "SALDO FINAL",
)
_DEBIT_DESCRIPTION_TOKENS = (
    "TARIFA",
    "DEB.",
    "DEB ",
    "DÉB.",
    "DÉB ",
    "PAGAMENTO",
    "CHEQUE PAGO",
    "RESGATE",
    "RETENCAO",
    "RETENÇÃO",
    "IRRF",
)
_CREDIT_DESCRIPTION_TOKENS = (
    "CRED.",
    "CRED ",
    "CRÉD.",
    "CRÉD ",
    "PIX RECEBIDO",
    "RECEBIMENTO PIX",
    "DEPOSITO",
    "DEP.",
    "DEP ",
    "CAPITALIZACAO",
    "CAPITALIZAÇÃO",
    "JUROS",
)


@dataclass(frozen=True, slots=True)
class SicoobLayoutParser:
    layout_names: frozenset[str] = _ALL_LAYOUTS

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        fallback_year = _resolve_fallback_year(lines, context=context)
        if layout_name == SICOOB_CREDIT_CARD_LAYOUT:
            rows = _parse_credit_card_rows(lines, fallback_year=fallback_year)
        elif layout_name in {SICOOB_HISTORY_LAYOUT, SICOOB_MONOSPACE_LAYOUT}:
            rows = _parse_current_account_cd_rows(lines, fallback_year=fallback_year)
            if not rows and layout_name == SICOOB_HISTORY_LAYOUT:
                rows = _parse_vertical_history_rows(lines, fallback_year=fallback_year)
        elif layout_name == SICOOB_FUTURE_CARD_LAYOUT:
            rows = _parse_future_card_rows(lines, fallback_year=fallback_year)
        elif layout_name == SICOOB_APPLICATIONS_LAYOUT:
            rows = _parse_application_rows(lines, fallback_year=fallback_year)
        elif layout_name == SICOOB_PIX_LIST_LAYOUT:
            rows = _parse_pix_rows(lines, fallback_year=fallback_year)
        elif layout_name == SICOOB_MODERN_CURRENT_LAYOUT:
            rows = _parse_modern_current_rows(lines, fallback_year=fallback_year)
            if not rows:
                rows = _parse_modern_columnar_rows(lines, fallback_year=fallback_year)
        elif layout_name == SICOOB_BOLETO_RECEIPT_LAYOUT:
            rows = _parse_boleto_receipt(lines)
        else:
            return None

        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser="layout_specific_sicoob",
            selection_reason=f"layout_specific_sicoob:{layout_name}",
        )


def _parse_credit_card_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    full_text = " ".join(line.text for line in lines)
    due_match = re.search(r"VENCIMENTO\s*:\s*\d{1,2}/(?P<month>\d{1,2})/(?P<year>\d{4})", full_text, re.I)
    due_month = int(due_match.group("month")) if due_match is not None else None
    due_year = int(due_match.group("year")) if due_match is not None else fallback_year
    rows: list[_ParsedTransaction] = []
    for line, match, amount_tokens in _iter_dated_amount_rows(lines):
        trailing_amount = _find_trailing_amount_token(match.group("rest"))
        if trailing_amount is None:
            continue
        amount_tokens = (trailing_amount,)
        description = _clean_description(match.group("rest"), amount_tokens=amount_tokens)
        normalized = normalize_text(description)
        if _is_opening_or_balance(normalized):
            continue
        amount_token = amount_tokens[-1]
        is_payment = "PAGAMENTO" in normalized
        amount = abs(parse_pdf_amount(amount_token.value)) if is_payment else -abs(parse_pdf_amount(amount_token.value))
        row_year = due_year
        row_month = int(match.group("date").split("/")[1])
        if due_month is not None and row_month > due_month:
            row_year -= 1
        rows.append(
            _build_row(
                date=_safe_parse_date(match.group("date"), fallback_year=row_year),
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                forced_sign=True,
            )
        )
    return rows


def _parse_current_account_cd_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line, match, amount_tokens in _iter_dated_amount_rows(lines):
        description = _clean_description(match.group("rest"), amount_tokens=amount_tokens)
        normalized = normalize_text(description)
        if _is_opening_or_balance(normalized):
            continue
        amount_token = amount_tokens[-1]
        amount = _resolve_semantic_amount(description, amount_token=amount_token)
        rows.append(
            _build_row(
                date=_safe_parse_date(match.group("date"), fallback_year=fallback_year),
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                forced_sign=not _amount_has_explicit_sign(amount_token.value),
            )
        )
    return rows


def _parse_vertical_history_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    blocks: list[tuple[str, _PdfLine, list[_PdfLine]]] = []
    in_transaction_scope = False
    for line in lines:
        normalized = normalize_text(line.text)
        if not in_transaction_scope:
            if "HIST" in normalized and "MOVIMENT" in normalized:
                in_transaction_scope = True
            continue
        if normalized == "RESUMO":
            break
        date_match = _DATE_ONLY_PATTERN.match(line.text)
        if date_match is not None:
            blocks.append((date_match.group("date"), line, []))
            continue
        if not blocks or _is_vertical_table_header(normalized):
            continue
        blocks[-1][2].append(line)

    rows: list[_ParsedTransaction] = []
    carried_amounts: list[AmountToken] = []
    for raw_date, source, content_lines in blocks:
        block_amounts = [
            token
            for content_line in content_lines
            for token in find_amount_tokens(content_line.text)
        ]
        # Textract can emit the next row's right-aligned value just before its date.
        amount_candidates = [*carried_amounts, *block_amounts]
        if not amount_candidates:
            continue
        amount_token = amount_candidates[0]
        carried_amounts = amount_candidates[1:]

        description = _clean_vertical_description(content_lines)
        normalized_description = normalize_text(description)
        if not description or _is_opening_or_balance(normalized_description):
            continue
        amount = _resolve_vertical_amount(
            description,
            amount_token=amount_token,
            content_lines=content_lines,
        )
        rows.append(
            _build_row(
                date=_safe_parse_date(raw_date, fallback_year=fallback_year),
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=source,
                forced_sign=True,
            )
        )
    return rows


def _parse_future_card_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line, match, amount_tokens in _iter_dated_amount_rows(lines):
        amount_token = _find_trailing_amount_token(match.group("rest"))
        if amount_token is None:
            continue
        amount_tokens = (amount_token,)
        rows.append(
            _build_row(
                date=_safe_parse_date(match.group("date"), fallback_year=fallback_year),
                description=_clean_description(match.group("rest"), amount_tokens=amount_tokens),
                amount=-abs(parse_pdf_amount(amount_token.value)),
                amount_token=amount_token,
                source=line,
                forced_sign=True,
            )
        )
    return rows


def _parse_application_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line, match, amount_tokens in _iter_dated_amount_rows(lines):
        description = _clean_description(match.group("rest"), amount_tokens=amount_tokens)
        normalized = normalize_text(description)
        if normalized == "APLICACAO FINANCEIRA" or not amount_tokens:
            continue
        amount_token = amount_tokens[0]
        amount = _resolve_semantic_amount(description, amount_token=amount_token)
        running_balance = parse_pdf_amount(amount_tokens[1].value) if len(amount_tokens) > 1 else None
        rows.append(
            _build_row(
                date=_safe_parse_date(match.group("date"), fallback_year=fallback_year),
                description=description,
                amount=amount,
                amount_token=amount_token,
                source=line,
                running_balance=running_balance,
                forced_sign=not _amount_has_explicit_sign(amount_token.value),
            )
        )
    return rows


def _parse_pix_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line in lines:
        match = _PIX_ROW_PATTERN.match(line.text)
        if match is None:
            continue
        amount_tokens = tuple(find_amount_tokens(match.group("rest")))
        if not amount_tokens:
            continue
        amount_token = amount_tokens[-1]
        description = _clean_description(match.group("rest"), amount_tokens=amount_tokens)
        rows.append(
            _build_row(
                date=_safe_parse_date(
                    f"{match.group('day')} {match.group('month')}",
                    fallback_year=fallback_year,
                ),
                description=description,
                amount=_resolve_semantic_amount(description, amount_token=amount_token),
                amount_token=amount_token,
                source=line,
                forced_sign=True,
            )
        )
    return rows


def _parse_modern_current_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    rows: list[_ParsedTransaction] = []
    for line, match, amount_tokens in _iter_dated_amount_rows(lines):
        description = _clean_description(match.group("rest"), amount_tokens=amount_tokens)
        if _is_opening_or_balance(normalize_text(description)):
            continue
        amount_token = amount_tokens[-1]
        rows.append(
            _build_row(
                date=_safe_parse_date(match.group("date"), fallback_year=fallback_year),
                description=description,
                amount=_resolve_semantic_amount(description, amount_token=amount_token),
                amount_token=amount_token,
                source=line,
                forced_sign=True,
            )
        )
    return rows


def _parse_modern_columnar_rows(lines: list[_PdfLine], *, fallback_year: int) -> list[_ParsedTransaction]:
    date_rows = _extract_modern_columnar_dates(lines)
    descriptions = _extract_modern_columnar_descriptions(lines)
    amounts = _extract_modern_columnar_amounts(lines, limit=len(date_rows))
    if not date_rows or len(date_rows) != len(descriptions) or len(date_rows) != len(amounts):
        return []

    rows: list[_ParsedTransaction] = []
    for (raw_date, source), description, amount_token in zip(
        date_rows,
        descriptions,
        amounts,
        strict=True,
    ):
        normalized_description = normalize_text(description)
        if _is_opening_or_balance(normalized_description):
            continue
        rows.append(
            _build_row(
                date=_safe_parse_date(raw_date, fallback_year=fallback_year),
                description=description,
                amount=_resolve_vertical_amount(
                    description,
                    amount_token=amount_token,
                    content_lines=[],
                ),
                amount_token=amount_token,
                source=source,
                forced_sign=True,
            )
        )
    return rows


def _extract_modern_columnar_dates(lines: list[_PdfLine]) -> list[tuple[str, _PdfLine]]:
    rows: list[tuple[str, _PdfLine]] = []
    in_date_column = False
    for line in lines:
        normalized = normalize_text(line.text)
        if not in_date_column:
            if normalized == "DATA DOCUMENTO":
                in_date_column = True
            continue
        if normalized == "RESUMO":
            break
        date_match = re.match(r"^\s*(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)(?:\s|$)", line.text)
        if date_match is not None:
            rows.append((date_match.group("date"), line))
    return rows


def _extract_modern_columnar_descriptions(lines: list[_PdfLine]) -> list[str]:
    descriptions: list[str] = []
    in_description_column = False
    for line in lines:
        normalized = normalize_text(line.text)
        if not in_description_column:
            if normalized.startswith("HIST") and "MOVIMENT" not in normalized:
                in_description_column = True
            continue
        if "CHEQUE ESPECIAL CONTRATADO" in normalized:
            break
        if _is_modern_columnar_description_anchor(normalized):
            descriptions.append(_strip_modern_ocr_marker(line.text))
        elif descriptions and line.text.strip():
            descriptions[-1] = f"{descriptions[-1]} {line.text.strip()}"
    return descriptions


def _extract_modern_columnar_amounts(lines: list[_PdfLine], *, limit: int) -> list[AmountToken]:
    amounts: list[AmountToken] = []
    in_amount_column = False
    for line in lines:
        normalized = normalize_text(line.text)
        if not in_amount_column:
            if normalized == "VALOR":
                in_amount_column = True
            continue
        amounts.extend(find_amount_tokens(line.text))
        if len(amounts) >= limit:
            return amounts[:limit]
    return amounts


def _is_modern_columnar_description_anchor(normalized: str) -> bool:
    return (
        "SALDO ANTERIOR" in normalized
        or "SALDO BLOQUEADO ANTERIOR" in normalized
        or "SALDO DO DIA" in normalized
        or ("PIX" in normalized and "RECEBIDO" in normalized)
        or "PARCELAS SUBSC" in normalized
        or ("APLICA" in normalized and "RDC" in normalized)
        or ("TRANSFER" in normalized and "POUPAN" in normalized)
    )


def _strip_modern_ocr_marker(value: str) -> str:
    return re.sub(r"^[^A-Za-zÀ-ÿ]+", "", value).strip()


def _parse_boleto_receipt(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    full_text = " ".join(line.text.strip() for line in lines if line.text.strip())
    date_match = re.search(r"DATA PAGAMENTO\s*:\s*(\d{1,2}/\d{1,2}/\d{4})", normalize_text(full_text))
    amount_match = re.search(r"VALOR PAGO\s*:\s*([\d.]+,\d{2})", normalize_text(full_text))
    if date_match is None or amount_match is None:
        return []
    beneficiary_match = re.search(
        r"BENEFICI[ÁA]RIO\s*:\s*(.+?)\s+NOME FANTASIA BENEFICI[ÁA]RIO",
        full_text,
        flags=re.IGNORECASE,
    )
    reference_match = re.search(r"NOSSO N[ÚU]MERO\s*:\s*([\w.-]+)", full_text, flags=re.IGNORECASE)
    beneficiary = beneficiary_match.group(1).strip() if beneficiary_match is not None else ""
    description = "PAGAMENTO DE BOLETO"
    if beneficiary:
        description = f"{description} {beneficiary}"
    source_page = lines[0].page_number if lines else None
    source_line = lines[0].line_number if lines else None
    return [
        build_parsed_transaction(
            date=_safe_parse_date(date_match.group(1), fallback_year=None),
            description=description,
            amount=-abs(parse_pdf_amount(amount_match.group(1))),
            source_page=source_page,
            source_line=source_line,
            external_reference_id=reference_match.group(1) if reference_match is not None else None,
            has_explicit_amount_sign=True,
        )
    ]


def _iter_dated_amount_rows(
    lines: list[_PdfLine],
) -> list[tuple[_PdfLine, re.Match[str], tuple[AmountToken, ...]]]:
    candidates: list[tuple[_PdfLine, re.Match[str], tuple[AmountToken, ...]]] = []
    for line in lines:
        match = _DATE_ROW_PATTERN.match(line.text)
        if match is None:
            continue
        amount_tokens = tuple(find_amount_tokens(match.group("rest")))
        if amount_tokens:
            candidates.append((line, match, amount_tokens))
    return candidates


def _resolve_fallback_year(lines: list[_PdfLine], *, context: LayoutSpecificParseContext) -> int:
    inferred = infer_default_statement_year_from_lines(lines)
    if inferred is not None:
        return inferred
    if context.reference_month_year is not None:
        return context.reference_month_year[1]
    return datetime.now(timezone.utc).year


def _resolve_semantic_amount(description: str, *, amount_token: AmountToken) -> float:
    raw_amount = parse_pdf_amount(amount_token.value)
    if _amount_has_explicit_sign(amount_token.value):
        return raw_amount
    normalized = normalize_text(description)
    if any(token in normalized for token in _DEBIT_DESCRIPTION_TOKENS):
        return -abs(raw_amount)
    if any(token in normalized for token in _CREDIT_DESCRIPTION_TOKENS):
        return abs(raw_amount)
    return raw_amount


def _build_row(
    *,
    date: str,
    description: str,
    amount: float,
    amount_token: AmountToken,
    source: _PdfLine,
    running_balance: float | None = None,
    forced_sign: bool = False,
) -> _ParsedTransaction:
    return build_parsed_transaction(
        date=date,
        description=description,
        amount=amount,
        source_page=source.page_number,
        source_line=source.line_number,
        running_balance=running_balance,
        has_explicit_amount_sign=forced_sign or _amount_has_explicit_sign(amount_token.value),
    )


def _safe_parse_date(raw_date: str, *, fallback_year: int | None) -> str:
    return parse_row_date(raw_date, fallback_year=fallback_year)


def _is_opening_or_balance(normalized_description: str) -> bool:
    return any(token in normalized_description for token in _OPENING_OR_BALANCE_TOKENS)


def _is_vertical_table_header(normalized: str) -> bool:
    return normalized in {"DATA", "HISTORICO", "VALOR"} or (
        "HIST" in normalized and "MOVIMENT" in normalized
    )


def _clean_vertical_description(lines: list[_PdfLine]) -> str:
    parts: list[str] = []
    for line in lines:
        normalized = normalize_text(line.text)
        if normalized in {"C", "D"} or _is_vertical_table_header(normalized):
            continue
        amount_tokens = tuple(find_amount_tokens(line.text))
        cleaned = _clean_description(line.text, amount_tokens=amount_tokens)
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts)


def _resolve_vertical_amount(
    description: str,
    *,
    amount_token: AmountToken,
    content_lines: list[_PdfLine],
) -> float:
    raw_amount = parse_pdf_amount(amount_token.value)
    suffix_match = re.search(r"([CD])\s*$", amount_token.value, flags=re.IGNORECASE)
    if suffix_match is not None:
        return -abs(raw_amount) if suffix_match.group(1).upper() == "D" else abs(raw_amount)
    standalone_sign = next(
        (
            normalize_text(line.text)
            for line in content_lines
            if normalize_text(line.text) in {"C", "D"}
        ),
        None,
    )
    if standalone_sign is not None:
        return -abs(raw_amount) if standalone_sign == "D" else abs(raw_amount)
    return _resolve_semantic_amount(description, amount_token=amount_token)


def _clean_description(raw_text: str, *, amount_tokens: tuple[AmountToken, ...]) -> str:
    value = raw_text
    for token in sorted(amount_tokens, key=lambda item: item.start, reverse=True):
        value = value[: token.start] + " " + value[token.end :]
    value = re.sub(r"(?:^|\s)[+\-]?\s*R\$\s*$", " ", value, flags=re.IGNORECASE)
    return " ".join(value.strip(" -|:").split())


def _find_trailing_amount_token(value: str) -> AmountToken | None:
    match = re.search(
        r"(?P<amount>[+\-]?\s*(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}\s*[CD]?)\s*$",
        value,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return AmountToken(value=match.group("amount"), start=match.start("amount"), end=match.end("amount"))


def _amount_has_explicit_sign(value: str) -> bool:
    return has_explicit_amount_sign(value) or bool(re.search(r"[CD]\s*$", value, flags=re.IGNORECASE))
