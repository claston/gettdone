"""Signed ledgers with dates on daily balance rows, shared by native and OCR."""
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _source
from app.application.parsers.pdf.native_table_source import native_header_columns, split_native_columns
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.source_regions import evidence_regions


def is_grouped_daily_cash(text):
    text = upper(text)
    return all(label in text for label in ("EXTRATO CONSOLIDADO", "SALDO INICIAL NO DIA",
        "SALDO FINAL NO DIA", "SALDO FINAL DO DIA"))


def cash(text):
    match = re.fullmatch(rf"([+-]?)\s*R\$\s*({NUMBER})", text.strip())
    if not match:
        fail("Um valor ou saldo está ilegível no documento.")
    return money(match[2]) * (-1 if match[1] == "-" else 1)


def parse_grouped_daily_cash(source):
    keys = ("HISTORICO", "TIPO", "VALOR", "SALDO")
    columns, blocks, current, date_evidence = None, [], None, None
    accounts = set()
    for page in source.pages:
        if not page.lines or any(not line.bbox for line in page.lines):
            fail("Uma página está sem as coordenadas necessárias para associar os movimentos.")
        text = "\n".join(line.text for line in page.lines)
        accounts.update(re.findall(r"CONTA:\s*([\d.-]+)\s+AGENCIA:\s*(\d+)", upper(text)))
        if len(accounts) > 1:
            fail("O extrato contém contas diferentes sem associação inequívoca dos movimentos.")
        found = native_header_columns(page, keys)
        columns = found or columns
        if columns is None:
            fail("Não foi possível localizar as colunas dos movimentos.")
        boundaries = [columns[key]-.015 for key in keys[1:]]
        page = split_native_columns(page, boundaries)
        tolerance = max(.003, min(line.bbox["height"] for line in page.lines if line.bbox)*.65)
        summaries = {}
        summary_bottom = 0
        for line in page.lines:
            match = re.fullmatch(rf"SALDO (INICIAL|FINAL) NO DIA ({DATE}):?", upper(line.text))
            if re.match(r"SALDO (INICIAL|FINAL) NO DIA", upper(line.text)) and not match:
                fail("A data do saldo do período está ilegível.")
            if match:
                summary_bottom = max(summary_bottom, line.bbox["top"]+tolerance)
                values = [cell for cell in page.lines if cell.bbox["left"] > line.bbox["left"]+.1
                    and abs(cell.bbox["top"]-line.bbox["top"]) < tolerance and "R$" in cell.text]
                if len(values) != 1:
                    fail("Um saldo do período está sem valor legível.")
                summaries[match[1]] = (day(match[2]), cash(values[0].text))
        if summaries:
            if set(summaries) != {"INICIAL", "FINAL"}:
                fail("O período dos saldos está incompleto ou ilegível.")
            if current is None or current["summary"] != summaries:
                current = {"summary": summaries, "items": []}
                blocks.append(current)
                date_evidence = None
        if current is None:
            fail("Falta o contexto do período para associar os movimentos.")
        headers = [line for line in page.lines if upper(line.text) == keys[0]]
        top = max((line.bbox["top"]+tolerance for line in headers), default=summary_bottom)
        footer = min((line.bbox["top"] for line in page.lines
            if line.bbox["top"] > top and (re.match(r"PAGINA\b", upper(line.text)) or (
                line.bbox["left"] >= boundaries[0] and "INSTITUICAO DE PAGAMENTO" in upper(line.text)))), default=1)-tolerance
        if any("R$" in line.text and line.bbox["top"] >= footer for line in page.lines):
            fail("Há valores após o rodapé identificado; a cobertura dos movimentos precisa ser conferida.")
        # TIPO identifies every movement/control, including a missing value.
        anchors = sorted((line for line in page.lines if line.bbox["top"] > top
            and boundaries[0] <= line.bbox["left"] < boundaries[1]
            and line.bbox["top"] < footer), key=lambda line: line.bbox["top"])
        if not anchors:
            fail("Uma página não contém movimentos ou controles legíveis.")
        used = set()
        for index, anchor in enumerate(anchors):
            end = anchors[index+1].bbox["top"]-tolerance if index+1 < len(anchors) else footer
            cells = [line for line in page.lines if anchor.bbox["top"]-tolerance <= line.bbox["top"] < end]
            used.update(line.id for line in cells)
            fields = [[line for line in cells if sum(line.bbox["left"] >= b for b in boundaries) == col]
                      for col in range(4)]
            if len(fields[3]) != 1:
                fail("Um movimento ou controle está sem saldo legível.")
            balance = cash(fields[3][0].text)
            control = upper(anchor.text)
            if control in {"SALDO FINAL DO DIA", "SALDO INICIAL DO DIA"}:
                if len(fields[0]) != 1 or not re.fullmatch(DATE, fields[0][0].text) or fields[2]:
                    fail("Uma data de agrupamento diário está ilegível.")
                stamp = day(fields[0][0].text)
                date_evidence = (stamp, page, fields[0][0])
                current["items"].append((control, stamp, balance))
                continue
            if date_evidence is None or not fields[0] or len(fields[2]) != 1:
                fail("Um movimento está sem data, descrição ou valor associado.")
            stamp, date_page, date_cell = date_evidence
            start, finish = current["summary"]["INICIAL"][0], current["summary"]["FINAL"][0]
            if not start <= stamp <= finish:
                fail("Uma data de movimento está fora do período informado.")
            amount = cash(fields[2][0].text)
            row = _source(_row(page.page_number, anchor.line_index, stamp,
                " ".join(line.text for line in fields[0])+" | "+anchor.text, amount, balance), page, cells)
            row = replace(row, source_regions=(*row.source_regions,
                *evidence_regions(date_page.page_number, [date_cell], words=date_page.words)),
                source_line_refs=(*row.source_line_refs, (date_page.page_number, date_cell.line_index)))
            current["items"].append(("movement", stamp, row))
        if any(line.id not in used for line in page.lines if top < line.bbox["top"] < footer
               and line.bbox["left"] >= boundaries[1]):
            fail("Há um valor ou saldo sem movimento associado.")
    rows, checks = [], 0
    blocks.sort(key=lambda block: block["summary"]["INICIAL"][0])
    continuity = True
    for index, block in enumerate(blocks):
        opening, closing = block["summary"]["INICIAL"][1], block["summary"]["FINAL"][1]
        running = opening
        last_date = None
        for kind, stamp, item in reversed(block["items"]):
            if last_date and stamp < last_date:
                fail("A ordem dos grupos diários não permite associar as datas com segurança.")
            last_date = stamp
            if kind == "movement":
                running += Decimal(str(item.transaction.amount))
                if running != Decimal(str(item.running_balance)):
                    financial_mismatch()
                rows.append(item)
            elif running != item:
                financial_mismatch()
            checks += 1
        if running != closing:
            financial_mismatch()
        if index:
            previous = blocks[index-1]["summary"]["FINAL"]
            delta = (date.fromisoformat(block["summary"]["INICIAL"][0])-date.fromisoformat(previous[0])).days
            continuity &= delta == 1 and previous[1] == opening
    if not rows:
        fail("Nenhum movimento foi identificado na tabela.")
    return VerifiedStatement(rows, "grouped_daily_signed_cash_v1",
        blocks[0]["summary"]["INICIAL"][1] if continuity else None,
        blocks[-1]["summary"]["FINAL"][1] if continuity else None,
        printed_balance_checks=checks, candidate_rows=len(rows), balance_checkpoints={},
        balance_bounds_unavailable=not continuity,
        opening_ambiguity_reason="statement_blocks_not_contiguous" if not continuity else "")
