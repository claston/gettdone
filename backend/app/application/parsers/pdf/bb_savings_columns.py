"""Savings value column, including a row physically continued on the next page."""

import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.native_table_source import split_native_columns
from app.application.parsers.pdf.row_geometry import nearest_row
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money


def is_bb_savings(text):
    return all(s in upper(text) for s in ("EXTRATOS - POUPANCA", "DT. BASE", "POUPANCA-OURO", "SALDO TOTAL"))


def parse_bb_savings(source):
    rows, opening, closing, carry = [], None, None, None
    value_left, balance_left, header_y = .815, .87, .27
    headings = [w for w in source.pages[0].words if w.bbox and upper(w.text) == "VALOR"]
    for heading in headings:
        balances = [w for w in source.pages[0].words if w.bbox and upper(w.text) == "SALDO"
                    and w.bbox["left"] > heading.bbox["left"]
                    and abs(w.bbox["top"]-heading.bbox["top"]) < .01]
        if len(balances) == 1:
            value_left, balance_left = heading.bbox["left"]-.025, balances[0].bbox["left"]-.008
            header_y = heading.bbox["top"] + heading.bbox["height"]
            break
    for original in source.pages:
        page = split_native_columns(original, [0.17, 0.25, 0.45, 0.59, value_left, balance_left])
        stop = min((line.bbox["top"] for line in page.lines if line.bbox and upper(line.text) == "SALDO ATUAL"), default=1)
        dates = [
            w
            for w in page.words
            if w.bbox
            and 0.08 < w.bbox["left"] < 0.14
            and re.fullmatch(DATE, w.text)
            and w.bbox["top"] < stop
            and (page.page_number > 1 or w.bbox["top"] > header_y)
        ]
        # LINE anchors retain source line numbers; date WORDs separate the two
        # independently printed dates when the content stream merged them.
        anchors = []
        for word in dates:
            parents = [line for line in page.lines if word.id in line.word_ids]
            if len(parents) != 1:
                fail()
            anchors.append(replace(parents[0], id=word.id, text=word.text, bbox=word.bbox, word_ids=(word.id,)))
        anchors.sort(key=lambda line: line.bbox["top"])
        cash = []
        for word in page.words:
            if (
                word.bbox
                and value_left < word.bbox["left"] < balance_left
                and word.bbox["top"] < stop
                and re.fullmatch(rf"{NUMBER}(?:[CD])?", word.text)
                and (page.page_number > 1 or word.bbox["top"] > header_y)
            ):
                parents = [line for line in page.lines if word.id in line.word_ids]
                if len(parents) != 1:
                    fail()
                cash.append(replace(parents[0], id=word.id, text=word.text, bbox=word.bbox, word_ids=(word.id,)))
        assigned = {line.id: nearest_row(line, anchors, max_distance=0.015) for line in cash}
        if carry:
            if not anchors or not 0.0 <= anchors[0].bbox["top"] < 0.12 or any(a is anchors[0] for a in assigned.values()):
                fail("A continuação da transação de poupança não pode ser confirmada.")
        for i, anchor in enumerate(anchors):
            associated = [line for line in cash if assigned[line.id] is anchor]
            history = [
                line
                for line in page.lines
                if line.bbox and 0.27 < line.bbox["left"] < value_left and nearest_row(line, anchors, max_distance=0.02) is anchor
            ]
            description = " ".join(line.text for line in sorted(history, key=lambda line: (line.bbox["top"], line.bbox["left"])))
            if "SALDO ANTERIOR" in upper(description):
                values = [
                    line
                    for line in page.lines
                    if line.bbox
                    and line.bbox["left"] > balance_left
                    and abs(line.bbox["top"] - anchor.bbox["top"]) < 0.015
                    and re.fullmatch(rf"{NUMBER}(?:\s*[CD])?", line.text)
                ]
                if len(values) != 1:
                    fail()
                token = re.fullmatch(rf"({NUMBER})(?:\s*([CD]))?", values[0].text)
                opening = money(token[1]) * (-1 if token[2] == "D" else 1)
                continue
            previous = carry if i == 0 else None
            cells = associated or ([previous[1]] if previous else [])
            if len(cells) != 1:
                fail("Uma transação de poupança está sem seu valor da coluna Valor.")
            value = cells[0]
            signs = [
                w
                for w in page.words
                if w.bbox
                and value_left < w.bbox["left"] < balance_left
                and w.text in {"C", "D"}
                and nearest_row(w, anchors, max_distance=0.015) is anchor
            ]
            printed = re.fullmatch(rf"({NUMBER})\s*([CD])?", value.text)
            directions = {w.text for w in signs} | ({printed[2]} if printed[2] else set())
            if len(directions) != 1 or not description:
                fail("O sinal ou o histórico da poupança não pode ser confirmado.")
            amount = money(printed[1]) * (-1 if directions.pop() == "D" else 1)
            row = _source(
                _row(page.page_number, anchor.line_index, day(anchor.text), description, amount), page, [anchor, *history, *associated]
            )
            if previous:
                prior_page, prior_value, prior_history = previous
                prior = _source(row, prior_page, [prior_value, *prior_history])
                row = replace(
                    row,
                    source_regions=[*prior.source_regions, *row.source_regions],
                    source_line_refs=(*prior.source_line_refs, *row.source_line_refs),
                    transaction=replace(row.transaction, description=" ".join(line.text for line in prior_history) + " " + description),
                )
                carry = None
            rows.append(row)
        orphan = [line for line in cash if assigned[line.id] is None]
        if orphan:
            if len(orphan) != 1 or orphan[0].bbox["top"] < 0.90 or carry:
                fail("Há um valor de poupança sem transação associada.")
            value = orphan[0]
            history = [
                line
                for line in page.lines
                if line.bbox and 0.27 < line.bbox["left"] < value_left and abs(line.bbox["top"] - value.bbox["top"]) < 0.008
            ]
            carry = (page, value, history)
        labels = [line for line in page.lines if upper(line.text) == "SALDO TOTAL"]
        for label in labels:
            values = [
                w
                for w in page.words
                if w.bbox and w.bbox["left"] > 0.5 and abs(w.bbox["top"] - label.bbox["top"]) < 0.008 and re.fullmatch(NUMBER, w.text)
            ]
            signs = [
                w
                for w in page.words
                if w.bbox and w.bbox["left"] > 0.5 and abs(w.bbox["top"] - label.bbox["top"]) < 0.008 and w.text in {"C", "D"}
            ]
            if len(values) == 1 and len(signs) == 1:
                closing = money(values[0].text) * (-1 if signs[0].text == "D" else 1)
    if carry or opening is None or closing is None or not rows:
        fail()
    if opening + sum((Decimal(str(r.transaction.amount)) for r in rows), Decimal(0)) != closing:
        financial_mismatch()
    return VerifiedStatement(rows, "bb_savings_native_value_columns_v1", opening, closing, printed_balance_checks=1)
