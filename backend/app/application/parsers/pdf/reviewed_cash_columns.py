"""Explicit financial columns and daily controls, independent of history keywords."""

import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.native_table_source import split_native_columns
from app.application.parsers.pdf.row_geometry import nearest_row
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.statement_row_context import (
    chronological_rows,
    contextual_row_date,
    labelled_row_period,
    validate_idle_day_controls,
)
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, fail, money

_AMOUNT = re.compile(rf"([+-]?)\s*(?:R\$\s*)?([+-]?)({NUMBER})\s*([CD])?", re.I)


def reviewed_cash_kind(text):
    text = upper(text)
    if "GERENCIADOR.CAIXA.GOV.BR/EMPRESA/DASHBOARD/EXTRATO/HISTORICO-ENRIQUECIDO" in text:
        return "caixa_enriched"
    if all(s in text for s in ("PAGME PAGAMENTOS", "CONTAPAG", "LANCAMENTO (R$)")):
        return "pagme"
    if "APLICATIVO SANTANDER EMPRESAS" in text and "VOCE ALCANCOU" not in text and "SALDO (R$)" in text:
        return "santander_app"
    if all(s in text for s in ("PLATAFORMA DE SERVICOS FINANCEIROS DO SICOOB", "COOPERATIVA:", "R$", "SALDO DO DIA")):
        return "sicoob_modern"
    return None


def supports_reviewed_cash_columns(source, kind):
    if kind != "sicoob_modern":
        return True
    from app.application.parsers.pdf.native_table_source import native_header_columns

    columns = native_header_columns(source.pages[0], ("DATA", "DOCUMENTO", "HISTORICO", "VALOR"))
    return bool(
        columns
        and columns["DATA"] < columns["DOCUMENTO"] < columns["HISTORICO"] < columns["VALOR"]
        and columns["DOCUMENTO"] < 0.18
        and 0.18 < columns["HISTORICO"] < 0.3
        and columns["VALOR"] > 0.8
    )


def _amount(cell):
    match = _AMOUNT.fullmatch(cell.text.strip())
    if not match or match[1] and match[2] or match[4] and (match[1] or match[2]):
        fail("Um valor da coluna financeira contém dígitos ou sinais ambíguos.")
    return money(match[3]) * (-1 if "-" in (match[1], match[2]) or upper(match[4] or "") == "D" else 1)


def parse_reviewed_cash(source, kind):
    history_left, value_left, balance_left, date_right = {
        "caixa_enriched": (0.24, 0.61, 0.79, 0.12),
        "pagme": (0.36, 0.68, 0.81, 0.13),
        "santander_app": (0.14, 0.68, 0.84, 0.13),
        "sicoob_modern": (0.21, 0.80, None, 0.105),
    }[kind]
    text = "\n".join(line.text for p in source.pages for line in p.lines)
    years = set(re.findall(r"(?:0[1-9]|[12]\d|3[01])/\d{2}/(\d{4})", text))
    year = years.pop() if len(years) == 1 else None
    period = labelled_row_period(text) if kind == "sicoob_modern" else (None, None)
    if kind == "sicoob_modern":
        # A lone expiry/issue year cannot replace a missing statement period.
        # Full transaction dates remain readable without optional coverage.
        year = None
    opening_position, row_positions = None, []
    rows, opening, controls, coverage = [], None, [], 0
    ended = False
    for original in source.pages:
        if ended:
            break
        boundaries = [date_right, history_left, value_left, *([balance_left] if balance_left else [])]
        page = split_native_columns(original, boundaries)
        headers = [
            line.bbox["top"]
            for line in page.lines
            if line.bbox and upper(line.text) in {"VALOR", "VALOR (R$)", "LANCAMENTO (R$)"} and line.bbox["left"] > value_left - 0.04
        ]
        low = min(headers) + 0.01 if headers else 0
        stops = [line.bbox["top"] for line in page.lines if line.bbox and upper(line.text) in {"RESUMO", "TOTALIZADORES"}]
        stop = min(stops, default=0.97)
        ended = bool(stops)
        body = [line for line in page.lines if line.bbox and low <= line.bbox["top"] < stop]
        # Printed opening often precedes the first table header.
        for label in page.lines:
            if "SALDO ANTERIOR" in upper(label.text):
                matches = [
                    line
                    for line in page.lines
                    if line.bbox
                    and line.bbox["left"] > value_left
                    and abs(line.bbox["top"] - label.bbox["top"]) < 0.006
                    and _AMOUNT.fullmatch(line.text.strip())
                ]
                if len(matches) == 1:
                    value = _amount(matches[0])
                    if opening is not None and opening != value:
                        fail("Há saldos anteriores conflitantes no mesmo extrato.")
                    opening = value
        anchors = sorted(
            [line for line in body if line.bbox["left"] < date_right and re.fullmatch(r"\d{2}/\d{2}(?:/\d{4})?", line.text)],
            key=lambda line: line.bbox["top"],
        )
        if not anchors:
            continue
        cash = [line for line in body if line.bbox["left"] >= value_left and re.search(r"\d,\d", line.text)]
        for index, anchor in enumerate(anchors):
            cells = [line for line in cash if nearest_row(line, anchors) is anchor]
            values = [line for line in cells if balance_left is None or line.bbox["left"] < balance_left]
            balances = [line for line in cells if balance_left is not None and line.bbox["left"] >= balance_left]
            high = (anchor.bbox["top"] + anchors[index + 1].bbox["top"]) / 2 if index + 1 < len(anchors) else stop
            lower = (anchors[index - 1].bbox["top"] + anchor.bbox["top"]) / 2 if index else anchor.bbox["top"] - 0.015
            history = [line for line in body if history_left <= line.bbox["left"] < value_left and lower <= line.bbox["top"] < high]
            # Complements belong to the current physical row until the next date,
            # whereas Santander/Pagme wrapped text can start just above its date.
            if kind in {"caixa_enriched", "sicoob_modern"}:
                high = anchors[index + 1].bbox["top"] - 0.004 if index + 1 < len(anchors) else stop
                history = [
                    line
                    for line in body
                    if history_left <= line.bbox["left"] < value_left and anchor.bbox["top"] - 0.004 <= line.bbox["top"] < high
                ]
            description = " ".join(line.text for line in sorted(history, key=lambda line: (line.bbox["top"], line.bbox["left"])))
            label = upper(description)
            if label.startswith("SALDO BLOQUEADO"):
                # Blocked deposits are a separate balance, never available cash.
                continue
            if label.startswith("SALDO"):
                control = balances if balance_left is not None else values
                if len(control) != 1 or balance_left is not None and values:
                    fail("Uma linha de saldo possui colunas financeiras ambíguas.")
                value = _amount(control[0])
                if "ANTERIOR" in label:
                    opening = value
                    opening_position = (page.page_number, index)
                else:
                    controls.append(
                        (
                            contextual_row_date(anchor.text, period, year)
                            if kind == "sicoob_modern" or year or len(anchor.text) == 10
                            else None,
                            value,
                        )
                    )
                continue
            if len(values) != 1 or balance_left is not None and len(balances) != 1 or not description:
                fail("Um movimento está sem sua descrição, valor ou saldo impresso.")
            date = contextual_row_date(anchor.text, period, year)
            coverage += 1
            row_positions.append((page.page_number, index))
            rows.append(
                _source(
                    _row(
                        page.page_number,
                        anchor.line_index,
                        date,
                        description,
                        _amount(values[0]),
                        _amount(balances[0]) if balances else None,
                    ),
                    page,
                    [anchor, *history, *cells],
                )
            )
    if not rows:
        fail()
    if kind == "sicoob_modern":
        rows = chronological_rows(rows, opening_position=opening_position, first_row_position=row_positions[0])
    elif kind == "santander_app":
        rows.reverse()
    if any(a.transaction.date > b.transaction.date for a, b in zip(rows, rows[1:])):
        fail("A ordem de datas da tabela não pode ser confirmada.")
    inferred = opening is None
    if inferred:
        if rows[0].running_balance is None:
            fail("O saldo anterior não pode ser identificado no documento.")
        opening = Decimal(str(rows[0].running_balance)) - Decimal(str(rows[0].transaction.amount))
    running, checks = opening, 0
    daily = {}
    for date, value in controls:
        if kind == "sicoob_modern":
            daily.setdefault(date, []).append(value)
        else:
            daily[date] = [value]
    if kind == "sicoob_modern":
        checks += validate_idle_day_controls(rows, opening, daily)
    for index, row in enumerate(rows):
        running += Decimal(str(row.transaction.amount))
        if row.running_balance is not None:
            if running != Decimal(str(row.running_balance)):
                financial_mismatch()
            checks += 1
        end_day = index + 1 == len(rows) or rows[index + 1].transaction.date != row.transaction.date
        if end_day and row.transaction.date in daily:
            values = daily[row.transaction.date]
            if any(running != value for value in values):
                financial_mismatch()
            rows[index] = replace(row, running_balance=float(values[0]) if len(set(values)) == 1 else None)
            checks += len(values)
    return VerifiedStatement(
        rows,
        f"{kind}_printed_cash_columns_v1",
        opening,
        running,
        opening_inferred=inferred,
        candidate_rows=coverage,
        printed_balance_checks=checks,
    )
