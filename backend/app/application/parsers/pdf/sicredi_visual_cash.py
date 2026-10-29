"""Sicredi image ledger: separate actual date/history/value/balance WORDs."""

import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.native_table_source import split_native_columns
from app.application.parsers.pdf.row_geometry import nearest_row
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

_VALUE = re.compile(rf"([+-]?)({NUMBER})(-?)")


def is_sicredi_visual_cash(source):
    text = upper("\n".join(line.text for p in source.pages for line in p.lines))
    if not all(s in text for s in ("SICREDI", "DADOS REFERENTES AO PERIODO", "COOPERATIVA:", "DOCUMENTO")):
        return False
    words = getattr(source.pages[0], "words", ())
    word_headers = {re.sub(r"\(R\$\)", "", upper(word.text)).strip() for word in words}
    return (
        bool(words)
        and {"DATA", "DOCUMENTO", "VALOR", "SALDO"} <= word_headers
        and _headers(source.pages[0], ("DATA", "DESCRICAO", "DOCUMENTO", "VALOR", "SALDO")) is None
        and all(s in text for s in ("SICREDI", "DADOS REFERENTES AO PERIODO", "COOPERATIVA:", "DOCUMENTO"))
    )


def _value(cell):
    match = _VALUE.fullmatch(cell.text.strip())
    if not match or match[1] and match[3]:
        fail("Uma célula Sicredi contém dígitos ou sinais ilegíveis.")
    return money(match[2]) * (-1 if "-" in (match[1], match[3]) else 1)


def parse_sicredi_visual_cash(source):
    first = source.pages[0]
    data_headers = [w for w in first.words if upper(w.text) == "DATA" and w.bbox and w.bbox["top"] < 0.22]
    if len(data_headers) != 1:
        fail("O cabeçalho da imagem Sicredi está ausente.")
    header_y = data_headers[0].bbox["top"]
    columns = {}
    for word in first.words:
        label = re.sub(r"\(R\$\)", "", upper(word.text)).strip()
        if label in {"DATA", "DOCUMENTO", "VALOR", "SALDO"} and word.bbox and abs(word.bbox["top"] - header_y) < 0.012:
            columns.setdefault(label, []).append(word)
    if any(len(columns.get(key, [])) != 1 for key in ("DATA", "DOCUMENTO", "VALOR", "SALDO")):
        fail("As colunas da imagem Sicredi não podem ser identificadas.")
    date_words = [word for word in first.words if word.bbox and word.bbox["left"] < 0.3 and re.fullmatch(DATE, word.text)]
    if not date_words:
        fail("A imagem Sicredi está sem datas de transação legíveis.")
    date_right = max(word.bbox["left"] + word.bbox["width"] for word in date_words) + 0.001
    doc_left = columns["DOCUMENTO"][0].bbox["left"] - 0.015
    value_left = columns["VALOR"][0].bbox["left"] - 0.015
    balance_left = (columns["VALOR"][0].bbox["left"] + columns["SALDO"][0].bbox["left"]) / 2 + 0.005
    rows, opening, running, checks = [], None, None, 0
    unreadable_balance = False
    for original in source.pages:
        page = split_native_columns(original, [date_right, doc_left, value_left, balance_left])
        header = max(
            (
                w.bbox["top"] + w.bbox["height"]
                for w in page.words
                if w.bbox and upper(w.text) in {"DATA", "DOCUMENTO"} and w.bbox["top"] < 0.22
            ),
            default=0,
        )
        stop = min((line.bbox["top"] for line in page.lines if upper(line.text).startswith("LANCAMENTOS FUTUROS")), default=0.96)
        body = [line for line in page.lines if line.bbox and header < line.bbox["top"] < stop]
        dates = sorted(
            [line for line in body if line.bbox["left"] < date_right and re.fullmatch(DATE, line.text)], key=lambda line: line.bbox["top"]
        )
        seeds = [
            line
            for line in body
            if upper(line.text) in {"SALDO", "SALDO ANTERIOR"} and (not dates or line.bbox["top"] < dates[0].bbox["top"])
        ]
        cash = [line for line in body if line.bbox["left"] >= value_left and re.search(r"\d[,.]\d", line.text)]
        anchors = [*dates, *seeds]
        assigned = {line.id: nearest_row(line, anchors, max_distance=0.014) for line in cash}
        if any(a is None for a in assigned.values()):
            fail("Há uma célula financeira Sicredi sem linha associada.")
        for seed in seeds:
            values = [line for line in cash if assigned[line.id] is seed]
            if len(values) != 1 or opening is not None:
                fail()
            opening = running = _value(values[0])
        for anchor in dates:
            cells = [line for line in cash if assigned[line.id] is anchor]
            values = [line for line in cells if line.bbox["left"] < balance_left]
            balances = [line for line in cells if line.bbox["left"] >= balance_left]
            if len(values) != 1 or len(balances) != 1 or running is None:
                fail("Uma transação Sicredi está sem valor ou saldo próprios.")
            history = [
                line
                for line in body
                if date_right <= line.bbox["left"] < doc_left and nearest_row(line, dates, max_distance=0.014) is anchor
            ]
            if not history:
                fail("O histórico de uma transação Sicredi está ilegível.")
            amount = _value(values[0])
            balance = _value(balances[0]) if _VALUE.fullmatch(balances[0].text.strip()) else None
            running += amount
            if balance is None:
                unreadable_balance = True
                financial_mismatch("Uma transação tem valor legível, mas seu controle de saldo exige revisão.")
            elif running != balance:
                financial_mismatch()
            if values[0].confidence is not None and values[0].confidence < 85:
                financial_mismatch("O OCR de um valor financeiro tem baixa confiança e exige revisão.")
            rows.append(
                _source(
                    _row(page.page_number, anchor.line_index, day(anchor.text), " ".join(line.text for line in history), amount, balance),
                    page,
                    [anchor, *history, *cells],
                )
            )
            checks += int(balance is not None)
    if not rows or opening is None:
        fail()
    return VerifiedStatement(
        rows,
        "sicredi_visual_signed_cash_v1",
        opening,
        Decimal(str(rows[-1].running_balance)) if rows[-1].running_balance is not None else None,
        printed_balance_checks=checks,
        balance_bounds_unavailable=unreadable_balance,
    )
