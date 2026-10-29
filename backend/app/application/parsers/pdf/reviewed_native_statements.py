"""Read original financial cells and wrapped names in explicit native layouts."""

import re
import unicodedata
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.native_table_source import native_header_columns, read_native_table_source, split_native_columns
from app.application.parsers.pdf.row_geometry import nearest_row
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

_CURRENCY = re.compile(rf"([+-]?)\s*R\$\s*([+-]?)({NUMBER})")


def _cash(line):
    match = _CURRENCY.fullmatch(line.text.strip())
    if not match or match[1] and match[2]:
        fail("Um valor da coluna financeira está sem sinal ou dígitos inequívocos.")
    return money(match[3]) * (-1 if "-" in (match[1], match[2]) else 1)


def parse_reviewed_native(raw, pages):
    if not raw.startswith(b"%PDF-"):
        return None
    text = upper("\n".join(pages))
    from app.application.parsers.pdf.bb_savings_columns import is_bb_savings, parse_bb_savings

    if is_bb_savings(text):
        source = read_native_table_source(raw)
        return parse_bb_savings(source), source
    if all(s in text for s in ("STONE", "EXTRATO DE CONTA CORRENTE", "CONTRAPARTE", "LANCAMENTO")):
        from app.application.parsers.pdf.followup_ocr import parse_stone_credit_debit

        source = read_native_table_source(raw)
        columns = native_header_columns(source.pages[0], ("DATA", "TIPO", "LANCAMENTO", "VALOR", "SALDO", "CONTRAPARTE"))
        if (
            "NAO HA DADOS A SEREM EXIBIDOS" in text
            or not columns
            or not (columns["DATA"] < 0.15 and 0.65 < columns["SALDO"] < 0.85 and columns["CONTRAPARTE"] > 0.82)
        ):
            return None
        source = replace(source, pages=[split_native_columns(p, [0.14, 0.23, 0.50, 0.70, 0.84]) for p in source.pages])
        return replace(parse_stone_credit_debit(source), opening_inferred=True), source
    kind = None
    if all(marker in text for marker in ("OUVIDORIA CREDISIS", "SALDO FINAL", "SALDOS EM")):
        kind = "credisis"
    elif all(marker in text for marker in ("CONSULTA DE PAGAMENTOS, TRANSFERENCIAS E PIX", "FAVORECIDO/BENEFICIARIO", "EFETUADO")):
        kind = "payment_names"
    elif all(marker in text for marker in ("DADOS GERAIS", "AGENCIA/CONTA", "SALDO TOTAL DISPONIVEL DIA", "SALDO DO DIA")):
        kind = "dated_cards"
    if kind is None:
        return None
    source = read_native_table_source(raw)
    statement = {"credisis": _credisis, "payment_names": _payments, "dated_cards": _cards}[kind](source)
    return statement, source


def _credisis(source):
    columns = native_header_columns(source.pages[0], ("DATA", "CODIGO", "DESCRICAO", "VALOR", "SALDO"))
    if not columns:
        fail("As colunas do extrato Credisis não podem ser separadas.")
    rows, opening, closing = [], None, None
    value_left = (columns["DESCRICAO"] + columns["VALOR"]) / 2
    balance_left = (columns["VALOR"] + columns["SALDO"]) / 2
    for original in source.pages:
        page = split_native_columns(original, [columns["CODIGO"] - 0.02, columns["DESCRICAO"] - 0.01, value_left, balance_left])
        stop = min((line.bbox["top"] for line in page.lines if upper(line.text).startswith("SALDOS EM ")), default=1)
        lines = [line for line in page.lines if line.bbox and line.bbox["top"] < stop]
        anchors = sorted(
            [
                line
                for line in lines
                if re.fullmatch(DATE, line.text)
                and line.bbox["left"] < columns["CODIGO"] - 0.02
                or upper(line.text) in {"SALDO ANTERIOR", "SALDO FINAL"}
            ],
            key=lambda line: line.bbox["top"],
        )
        cash = [line for line in lines if line.bbox["left"] >= value_left and re.search(r"\d,\d", line.text)]
        assigned = {line.id: nearest_row(line, anchors) for line in cash}
        for i, anchor in enumerate(anchors):
            cells = [line for line in cash if assigned[line.id] is anchor]
            if upper(anchor.text) in {"SALDO ANTERIOR", "SALDO FINAL"}:
                if len(cells) != 1:
                    fail()
                if upper(anchor.text) == "SALDO ANTERIOR":
                    opening = _cash(cells[0])
                else:
                    closing = _cash(cells[0])
                continue
            values = [line for line in cells if line.bbox["left"] < balance_left]
            balances = [line for line in cells if line.bbox["left"] >= balance_left]
            if len(values) != 1 or len(balances) != 1:
                fail("Uma linha Credisis está sem valor ou saldo próprio.")
            high = anchors[i + 1].bbox["top"] - 0.003 if i + 1 < len(anchors) else stop
            history = [
                line
                for line in lines
                if columns["DESCRICAO"] - 0.01 <= line.bbox["left"] < value_left and anchor.bbox["top"] - 0.003 <= line.bbox["top"] < high
            ]
            references = [
                line
                for line in lines
                if columns["CODIGO"] - 0.02 <= line.bbox["left"] < columns["DESCRICAO"] - 0.01
                and abs(line.bbox["top"] - anchor.bbox["top"]) < 0.003
            ]
            if not history or len(references) != 1:
                fail("A descrição ou a referência Credisis não pode ser associada à linha.")
            rows.append(
                _source(
                    _row(
                        page.page_number,
                        anchor.line_index,
                        day(anchor.text),
                        " ".join(line.text for line in history),
                        _cash(values[0]),
                        _cash(balances[0]),
                        references[0].text,
                    ),
                    page,
                    [anchor, *history, *cells, *references],
                )
            )
    if opening is None or closing is None or not rows:
        fail()
    rows.reverse()
    running = opening
    for row in rows:
        running += Decimal(str(row.transaction.amount))
        if running != Decimal(str(row.running_balance)):
            financial_mismatch()
    if running != closing:
        financial_mismatch()
    return VerifiedStatement(rows, "credisis_native_signed_cash_v1", opening, closing, printed_balance_checks=len(rows))


def _payments(source):
    rows = []
    for original in source.pages:
        page = split_native_columns(original, [0.23, 0.35, 0.46, 0.60])
        dates = [line for line in page.lines if line.bbox and 0.5 < line.bbox["left"] < 0.73 and re.fullmatch(DATE, line.text)]
        values = [
            line for line in page.lines if line.bbox and line.bbox["left"] > 0.73 and re.fullmatch(rf"{NUMBER}\s+efetuado", line.text, re.I)
        ]
        if len(dates) != len(values) or not dates:
            fail("Uma linha do relatório de pagamentos está sem data ou valor efetuado.")
        for value in values:
            anchor = nearest_row(value, dates)
            names = [
                line
                for line in page.lines
                if line.bbox
                and line.bbox["left"] < 0.23
                and nearest_row(line, dates) is anchor
                and abs(line.bbox["top"] - anchor.bbox["top"]) < 0.015
            ]
            if not names:
                fail("Um beneficiário do relatório de pagamentos está ilegível.")
            names.sort(key=lambda line: (line.bbox["top"], line.bbox["left"]))
            details = [
                line
                for line in page.lines
                if line.bbox
                and 0.23 <= line.bbox["left"] < 0.60
                and nearest_row(line, dates) is anchor
                and abs(line.bbox["top"] - anchor.bbox["top"]) < 0.015
            ]
            details.sort(key=lambda line: (sum(line.bbox["left"] >= boundary for boundary in (0.35, 0.46)), line.bbox["top"]))
            references = [
                line.text
                for line in details
                if line.bbox["left"] >= 0.46 and re.fullmatch(r"[A-Za-z0-9._-]{1,40}", line.text) and line.text != "-"
            ]
            rows.append(
                _source(
                    _row(
                        page.page_number,
                        anchor.line_index,
                        day(anchor.text),
                        " ".join(line.text for line in [*names, *details]),
                        -money(value.text.split()[0]),
                        reference=references[0] if len(references) == 1 else None,
                    ),
                    page,
                    [anchor, *names, *details, value],
                )
            )
    return VerifiedStatement(rows, "itau_payment_beneficiary_columns_v1", None, None, balance_bounds_unavailable=True)


def _cards(source):
    rows, current, opening, running, closing, checks = [], None, None, None, None, 0
    for page in source.pages:
        dates = [line for line in page.lines if line.bbox and 0.35 < line.bbox["left"] < 0.65 and re.fullmatch(DATE, line.text)]
        cash = [line for line in page.lines if line.bbox and line.bbox["left"] > 0.6 and _CURRENCY.fullmatch(line.text.strip())]
        history = [
            line
            for line in page.lines
            if line.bbox and line.bbox["left"] < 0.4 and any(abs(line.bbox["top"] - value.bbox["top"]) < 0.015 for value in cash)
        ]
        if len(cash) != len(history):
            fail("Um cartão do extrato está sem sua descrição ou valor financeiro.")
        for anchor in sorted(history, key=lambda line: line.bbox["top"]):
            for label in dates:
                if label.bbox["top"] < anchor.bbox["top"]:
                    current = day(label.text)
            values = [line for line in cash if nearest_row(line, history) is anchor]
            if len(values) != 1 or current is None:
                fail("Uma transação está sem a data ou o valor do seu cartão.")
            description = "".join(c for c in anchor.text if unicodedata.category(c) != "Co").strip()
            amount = _cash(values[0])
            if upper(description) in {"SALDO DO DIA", "SALDO TOTAL DISPONIVEL DIA"}:
                if opening is None:
                    opening = running = amount
                elif running != amount:
                    financial_mismatch()
                closing = amount
                checks += 1
                continue
            if opening is None:
                fail("O extrato começa sem seu saldo de referência.")
            running += amount
            rows.append(_source(_row(page.page_number, anchor.line_index, current, description, amount), page, [anchor, values[0]]))
    if not rows or closing is None:
        fail()
    return VerifiedStatement(rows, "dated_native_cash_cards_v1", opening, closing, printed_balance_checks=checks)
