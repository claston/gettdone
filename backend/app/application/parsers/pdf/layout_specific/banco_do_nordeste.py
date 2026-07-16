from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.application.normalization.pdf_amount_tokens import has_explicit_amount_sign, parse_pdf_amount
from app.application.normalization.pdf_row_match_rules import is_amount_only_row
from app.application.normalization.pdf_text_rules import apply_sign_hints
from app.application.parsers.pdf.layout_specific.contract import (
    LayoutSpecificParseContext,
    LayoutSpecificParseResult,
)
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction, normalize_text
from app.application.parsers.pdf.models import _ParsedTransaction, _PdfLine

BANCO_NORDESTE_EXTRATO_CONSOLIDADO_LAYOUT = "banco_do_nordeste_extrato_consolidado_v1"
BANCO_NORDESTE_FUNDOS_RENTABILIDADE_LAYOUT = "banco_do_nordeste_fundos_investimentos_rentabilidade_v1"

_PT_BR_FULL_MONTHS = {
    "JANEIRO": 1,
    "FEVEREIRO": 2,
    "MARCO": 3,
    "ABRIL": 4,
    "MAIO": 5,
    "JUNHO": 6,
    "JULHO": 7,
    "AGOSTO": 8,
    "SETEMBRO": 9,
    "OUTUBRO": 10,
    "NOVEMBRO": 11,
    "DEZEMBRO": 12,
}


@dataclass(frozen=True, slots=True)
class BancoDoNordesteLayoutParser:
    layout_names: frozenset[str] = frozenset(
        {
            BANCO_NORDESTE_EXTRATO_CONSOLIDADO_LAYOUT,
            BANCO_NORDESTE_FUNDOS_RENTABILIDADE_LAYOUT,
        }
    )

    def parse(
        self,
        *,
        layout_name: str,
        lines: list[_PdfLine],
        context: LayoutSpecificParseContext,
    ) -> LayoutSpecificParseResult | None:
        if layout_name == BANCO_NORDESTE_EXTRATO_CONSOLIDADO_LAYOUT:
            rows = _parse_extrato_consolidado_rows(lines)
            selected_parser = "layout_specific_banco_nordeste_consolidado"
            selection_reason = "layout_specific_banco_nordeste_consolidado"
        elif layout_name == BANCO_NORDESTE_FUNDOS_RENTABILIDADE_LAYOUT:
            rows = _parse_fundos_rentabilidade_rows(
                lines,
                reference_month_year=context.reference_month_year,
            )
            selected_parser = "layout_specific_banco_nordeste_fundos_rentabilidade"
            selection_reason = "layout_specific_banco_nordeste_fundos_rentabilidade"
        else:
            return None
        if not rows:
            return None
        return LayoutSpecificParseResult(
            rows=rows,
            selected_parser=selected_parser,
            selection_reason=selection_reason,
        )


def _parse_extrato_consolidado_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    movement_lines = _slice_movimentacao_lines(lines)
    statement_month_year = _infer_month_year(lines)
    if not movement_lines or statement_month_year is None:
        return []

    statement_month, statement_year = statement_month_year
    parsed_rows: list[_ParsedTransaction] = []
    previous_running_balance: float | None = None
    index = 0

    while index < len(movement_lines):
        line = movement_lines[index]
        if not _is_day_line(line.text):
            index += 1
            continue

        current_date = _build_iso_date(day=line.text.strip(), month=statement_month, year=statement_year)
        next_index = index + 1
        if next_index >= len(movement_lines):
            break

        description = movement_lines[next_index].text.strip()
        normalized_description = normalize_text(description)
        next_index += 1
        if not description or _is_day_line(description):
            index += 1
            continue

        document_value: str | None = None
        amount_value: float | None = None

        if next_index < len(movement_lines) and is_amount_only_row(movement_lines[next_index].text.strip()):
            amount_value = parse_pdf_amount(movement_lines[next_index].text.strip())
            next_index += 1
        elif next_index + 1 < len(movement_lines):
            candidate_document = movement_lines[next_index].text.strip()
            candidate_amount = movement_lines[next_index + 1].text.strip()
            if re.fullmatch(r"\d{1,12}", re.sub(r"\D", "", candidate_document)) and is_amount_only_row(
                candidate_amount
            ):
                document_value = re.sub(r"\D", "", candidate_document)
                amount_value = parse_pdf_amount(candidate_amount)
                next_index += 2

        if amount_value is None or next_index >= len(movement_lines):
            index += 1
            continue
        if not is_amount_only_row(movement_lines[next_index].text.strip()):
            index += 1
            continue

        running_balance = parse_pdf_amount(movement_lines[next_index].text.strip())
        next_index += 1

        if normalized_description.startswith("SALDO ANTERIOR") or normalized_description.startswith("SALDO INICIAL"):
            parsed_rows.append(
                build_parsed_transaction(
                    date=current_date,
                    description="SALDO ANTERIOR" if "ANTERIOR" in normalized_description else "SALDO INICIAL",
                    amount=running_balance,
                    source_page=line.page_number,
                    source_line=line.line_number,
                    running_balance=running_balance,
                    has_explicit_amount_sign=has_explicit_amount_sign(str(amount_value)),
                )
            )
            previous_running_balance = running_balance
            index = next_index
            continue

        full_description = description if not document_value else f"{description} {document_value}".strip()
        signed_amount = _resolve_signed_amount(
            raw_amount=amount_value,
            description=full_description,
            previous_running_balance=previous_running_balance,
            running_balance=running_balance,
        )
        parsed_rows.append(
            build_parsed_transaction(
                date=current_date,
                description=full_description,
                amount=signed_amount,
                source_page=line.page_number,
                source_line=line.line_number,
                running_balance=running_balance,
                external_reference_id=document_value,
                has_explicit_amount_sign=has_explicit_amount_sign(str(amount_value)),
            )
        )
        previous_running_balance = running_balance
        index = next_index

    return parsed_rows


def _parse_fundos_rentabilidade_rows(
    lines: list[_PdfLine],
    *,
    reference_month_year: tuple[int, int] | None,
) -> list[_ParsedTransaction]:
    movement_lines = _slice_fundos_rentabilidade_lines(lines)
    statement_month_year = _infer_month_year(lines) or reference_month_year
    if not movement_lines or statement_month_year is None:
        return []

    statement_month, statement_year = statement_month_year
    parsed_rows: list[_ParsedTransaction] = []
    index = 0

    while index < len(movement_lines):
        line = movement_lines[index]
        inline_row = _parse_fundos_rentabilidade_line(line, month=statement_month, year=statement_year)
        if inline_row is not None:
            parsed_rows.append(inline_row)
            index += 1
            continue

        if not _is_day_line(line.text):
            index += 1
            continue

        next_index = index + 1
        if next_index >= len(movement_lines):
            break

        description = re.sub(r"\s+", " ", movement_lines[next_index].text).strip()
        if not description or _is_day_line(description):
            index += 1
            continue
        next_index += 1

        while next_index < len(movement_lines) and _is_quantity_or_unit_line(movement_lines[next_index].text):
            next_index += 1

        if next_index >= len(movement_lines) or not is_amount_only_row(movement_lines[next_index].text.strip()):
            index += 1
            continue

        amount_value = abs(parse_pdf_amount(movement_lines[next_index].text.strip()))
        parsed_rows.append(
            build_parsed_transaction(
                date=_build_iso_date(day=line.text.strip(), month=statement_month, year=statement_year),
                description=_normalize_fundos_description(description),
                amount=_resolve_fundos_signed_amount(amount=amount_value, description=description),
                source_page=line.page_number,
                source_line=line.line_number,
                has_explicit_amount_sign=False,
            )
        )
        index = next_index + 1

    return parsed_rows


def _slice_movimentacao_lines(lines: list[_PdfLine]) -> list[_PdfLine]:
    start_index: int | None = None
    for index, line in enumerate(lines):
        if normalize_text(line.text).endswith("DEMONSTRATIVO DA MOVIMENTACAO DE CONTA CORRENTE"):
            start_index = index + 1
            break
    if start_index is None:
        return []
    return [
        line
        for line in lines[start_index:]
        if normalize_text(line.text) not in {"DIA", "HISTORICO", "DOCUMENTO", "VALOR", "SALDO"}
    ]


def _slice_fundos_rentabilidade_lines(lines: list[_PdfLine]) -> list[_PdfLine]:
    start_index: int | None = None
    for index, line in enumerate(lines):
        if "MOVIMENTACOES BNB" in normalize_text(line.text):
            start_index = index + 1
            break
    if start_index is None:
        return []

    movement_lines: list[_PdfLine] = []
    for line in lines[start_index:]:
        normalized_line = normalize_text(line.text)
        if normalized_line in {
            "DIA HISTORICO QUANT. COTAS VALOR COTA VALOR EM R$",
            "DIA HISTORICO QUANT. COTAS VALOR COTA VALOR EM R $",
            "DIA",
            "HISTORICO",
            "QUANT. COTAS",
            "VALOR COTA",
            "VALOR EM R$",
            "VALOR EM R $",
        }:
            continue
        if normalized_line:
            movement_lines.append(line)
    return movement_lines


def _infer_month_year(lines: list[_PdfLine]) -> tuple[int, int] | None:
    for line in lines:
        match = re.search(
            r"\b(JANEIRO|FEVEREIRO|MARCO|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)/(\d{4})\b",
            normalize_text(line.text),
        )
        if match:
            month = _PT_BR_FULL_MONTHS.get(match.group(1))
            if month is not None:
                return month, int(match.group(2))
    return None


def _parse_fundos_rentabilidade_line(line: _PdfLine, *, month: int, year: int) -> _ParsedTransaction | None:
    match = re.match(
        r"^(?P<day>\d{1,2})\s+(?P<rest>.+?)\s+(?P<amount>\d+(?:\.\d{3})*,\d{2})$",
        line.text.strip(),
    )
    if match is None:
        return None

    description = re.sub(r"\s+\d[\d\.,]*\s+\d[\d\.,]*\s*$", "", match.group("rest")).strip()
    if not description:
        return None
    amount_value = abs(parse_pdf_amount(match.group("amount")))
    return build_parsed_transaction(
        date=_build_iso_date(day=match.group("day"), month=month, year=year),
        description=_normalize_fundos_description(description),
        amount=_resolve_fundos_signed_amount(amount=amount_value, description=description),
        source_page=line.page_number,
        source_line=line.line_number,
        has_explicit_amount_sign=False,
    )


def _resolve_fundos_signed_amount(*, amount: float, description: str) -> float:
    normalized_description = normalize_text(description)
    normalized_sign_description = normalized_description.replace(".", "")
    if normalized_description.startswith("SALDO INICIAL") or normalized_description.startswith("SALDO ANTERIOR"):
        return amount
    if normalized_description.startswith("APLICACAO"):
        return -amount
    if (
        "RESGATE" in normalized_sign_description
        and "IOF" not in normalized_sign_description
        and "IR FEDERAL" not in normalized_sign_description
    ):
        return amount
    if "IOF" in normalized_sign_description or "IR FEDERAL" in normalized_sign_description:
        return -amount
    return apply_sign_hints(amount, description, None)


def _normalize_fundos_description(description: str) -> str:
    if normalize_text(description).startswith("SALDO INICIAL"):
        return "SALDO INICIAL"
    return re.sub(r"\s+", " ", description).strip()


def _is_quantity_or_unit_line(raw: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d{3})*,\d{3,6}", raw.strip()))


def _build_iso_date(*, day: str, month: int, year: int) -> str:
    return datetime(year, month, int(day)).strftime("%Y-%m-%d")


def _is_day_line(raw: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}", raw.strip()))


def _resolve_signed_amount(
    *,
    raw_amount: float,
    description: str,
    previous_running_balance: float | None,
    running_balance: float,
) -> float:
    amount = raw_amount
    if previous_running_balance is not None:
        if abs((previous_running_balance + amount) - running_balance) <= 0.02:
            return amount
        if abs((previous_running_balance - abs(amount)) - running_balance) <= 0.02:
            return -abs(amount)
        if abs((previous_running_balance + abs(amount)) - running_balance) <= 0.02:
            return abs(amount)
    return apply_sign_hints(amount, description, None)
