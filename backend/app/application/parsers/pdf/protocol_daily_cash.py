"""Signed protocol tables whose daily closing balance precedes that day's rows."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.native_table_source import native_header_columns, split_native_columns
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.pdf_document_segmentation import segment_pdf_pages


def is_protocol_daily_cash(text):
    text = upper(text)
    return all(label in text for label in ("EXTRATO FINANCEIRO", "PROTOCOLO", "SALDO DO DIA", "VALOR (R$)"))


def parse_protocol_daily_cash(source):
    texts = ["\n".join(line.text for line in page.lines) for page in source.pages]
    segment_pdf_pages(texts)
    keys = ("DATA", "DESCRICAO", "PROTOCOLO", "VALOR")
    columns, rows, groups, current = None, [], {}, None
    for page in source.pages:
        columns = native_header_columns(page, keys) or columns
        if columns is None or not all(line.bbox for line in page.lines):
            fail("Não foi possível associar as colunas de todos os lançamentos.")
        # Numeric protocols are wider than their centered header label.
        boundaries = [columns["DESCRICAO"] - .02, columns["PROTOCOLO"] - .05, columns["VALOR"] - .03]
        page = split_native_columns(page, boundaries)
        header = max((line.bbox["top"] + line.bbox.get("height", 0) for line in page.lines
                      if upper(line.text) == "PROTOCOLO"), default=0)
        body = [line for line in page.lines if line.bbox["top"] > header + .002]
        anchors = sorted((line for line in body if line.bbox["left"] < boundaries[0]), key=lambda line: line.bbox["top"])
        anchors = [line for line in anchors if re.search(r"\d/", line.text)]
        cash = [line for line in body if line.bbox["left"] >= boundaries[-1]]
        used = set()
        if not anchors:
            fail("Uma página está sem datas de lançamentos legíveis.")
        for index, anchor in enumerate(anchors):
            if not re.fullmatch(DATE, anchor.text):
                fail("Uma data de lançamento está ilegível.")
            stamp = day(anchor.text)
            if current and stamp < current:
                fail("Não foi possível confirmar a ordem dos dias do extrato.")
            current = stamp
            end = anchors[index + 1].bbox["top"] - .004 if index + 1 < len(anchors) else 1
            cells = [line for line in body if anchor.bbox["top"] - .004 <= line.bbox["top"] < end]
            values = [line for line in cells if line.bbox["left"] >= boundaries[-1]]
            history = [line for line in cells if boundaries[0] <= line.bbox["left"] < boundaries[1]]
            references = [line for line in cells if boundaries[1] <= line.bbox["left"] < boundaries[2]]
            if len(values) != 1 or not history:
                fail("Um lançamento está sem valor ou descrição legível.")
            value = values[0]
            used.add(value.id)
            description = " ".join(line.text for line in sorted(history, key=lambda line: line.bbox["top"]))
            group = groups.setdefault(stamp, {"closing": None, "rows": [], "control": None})
            if upper(description) == "SALDO DO DIA":
                if references or not re.fullmatch(rf"[+-]?{NUMBER}", value.text):
                    fail("Um saldo diário está ilegível ou ambíguo.")
                closing = money(value.text)
                if group["closing"] is not None and group["closing"] != closing:
                    fail("Há saldos diferentes para o mesmo dia.")
                group.update(closing=closing, control=(page, cells))
                continue
            if (not re.fullmatch(rf"[+-]{NUMBER}", value.text) or len(references) != 1
                    or not re.fullmatch(r"\d+", references[0].text)):
                fail("Um lançamento está sem valor, sinal ou protocolo legível.")
            group["rows"].append(len(rows))
            rows.append(_source(_row(page.page_number, anchor.line_index, stamp, description, money(value.text),
                                     reference=references[0].text), page, cells))
        if used != {line.id for line in cash}:
            fail("Há valores sem uma data de lançamento associada.")
    if not rows or any(group["closing"] is None for group in groups.values()):
        fail("Falta o saldo diário de um trecho do extrato.")
    opening = previous = None
    checks = 0
    for group in groups.values():
        total = sum((Decimal(str(rows[index].transaction.amount)) for index in group["rows"]), Decimal(0))
        if previous is None:
            opening = group["closing"] - total
        else:
            if previous + total != group["closing"]:
                financial_mismatch("Os lançamentos não conferem com um dos saldos diários impressos.")
            checks += 1
        previous = group["closing"]
        if group["rows"]:
            index = group["rows"][-1]
            row = rows[index]
            evidence = _source(row, *group["control"])
            rows[index] = replace(row, running_balance=float(previous),
                source_regions=[*row.source_regions, *evidence.source_regions],
                source_line_refs=(*row.source_line_refs, *evidence.source_line_refs))
    return VerifiedStatement(rows, "protocol_daily_signed_cash_v1", opening, previous,
        partial=validate_statement_completeness(texts), opening_inferred=True,
        printed_balance_checks=checks, candidate_rows=len(rows), balances_are_printed=True)
