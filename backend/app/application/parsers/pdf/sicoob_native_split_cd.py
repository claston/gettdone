"""SISBR native history preserves detached C/D signs, including page breaks."""
import re
from dataclasses import replace
from decimal import Decimal
from statistics import median

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.native_table_source import native_header_columns, split_native_columns
from app.application.parsers.pdf.page_financial_fragments import preceding_page_amounts
from app.application.parsers.pdf.sicoob_cash_controls import is_blocked_cheque_history
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.statement_row_context import chronological_rows, validate_idle_day_controls
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

_VALUE = re.compile(rf"({NUMBER})\s*([CD*]?)")


def _attach_evidence(row, band):
    groups = {}
    for _, page, line in band:
        groups.setdefault(page.page_number, (page, []))[1].append(line)
    regions, refs = list(row.source_regions), list(row.source_line_refs)
    for page, cells in groups.values():
        evidence = _source(row, page, cells)
        regions.extend(evidence.source_regions)
        refs.extend(evidence.source_line_refs)
    return replace(row, source_regions=regions, source_line_refs=tuple(refs))


def parse_sicoob_native_split_cd(source):
    columns = _headers(source.pages[0], ("DATA", "HISTORICO", "VALOR"))
    value_left = columns["VALOR"] - .04 if columns else None
    if source.provider == "native_pdfium_text":
        columns = native_header_columns(source.pages[0], ("DATA", "HISTORICO", "VALOR"))
        if columns:
            # Amounts are right aligned; long values legitimately start before
            # the heading. Require their boxes to reach the financial column.
            value_left = min([columns["VALOR"] - .04, *[
                cell.bbox["left"] for page in source.pages for cell in (*page.lines, *page.words)
                if cell.bbox and _VALUE.fullmatch(upper(cell.text))
                and cell.bbox["left"] > (columns["HISTORICO"] + columns["VALOR"]) / 2
                and cell.bbox["left"] + cell.bbox["width"] >= columns["VALOR"]]])
            source = replace(source, pages=[split_native_columns(page,
                [columns["HISTORICO"] - .02, value_left],
                include=lambda line: bool(re.match(r"^\d{2}/\d{2}\s+", line.text)
                    or upper(line.text).startswith("DATA ") and "HISTORICO" in upper(line.text))) for page in source.pages])
    if not columns:
        labels = [upper(line.text) for page in source.pages for line in page.lines]
        dated = [text for text in labels if re.match(r"^\d{2}/\d{2}(?:\s|$)", text)]
        if (any(text.endswith("DATA HISTORICO VALOR") for text in labels) and dated
                and all(re.fullmatch(rf"\d{{2}}/\d{{2}}\s+.+\s+{NUMBER}\s*[CD*]", text) for text in dated)):
            return None  # The complete flowing-text form has explicit inline signs.
        fail("O SISBR está sem as três colunas de histórico.")
    text = upper("\n".join(line.text for page in source.pages for line in page.lines))
    periods = set(re.findall(rf"PERIODO:\s*({DATE})\s*-\s*({DATE})", text))
    accounts = set(re.findall(r"(?m)^CONTA:\s*([\d.-]+)", "\n".join(upper(line.text) for p in source.pages for line in p.lines)))
    if len(periods) != 1 or len(accounts) != 1:
        fail("A conta ou o período SISBR não pode ser confirmado.")
    first, last = next(iter(periods))
    start, end = day(first), day(last)
    if start > end:
        fail()
    first_header = next(line for line in source.pages[0].lines if upper(line.text) == "DATA")
    witnesses = [(p.page_number + line.bbox["top"], p, line) for p in source.pages for line in p.lines]
    summary = [(pos, p, line) for pos, p, line in witnesses if upper(line.text) == "RESUMO"]
    if len(summary) != 1:
        fail("O resumo final SISBR não está legível.")
    end_pos, last_page, _ = summary[0]
    body = [(pos, p, line) for pos, p, line in witnesses
            if 1+first_header.bbox["top"]+.005 < pos < end_pos]
    anchors = sorted((item for item in body if item[2].bbox["left"] < columns["HISTORICO"]-.02
                      and re.fullmatch(r"\d{2}/\d{2}", item[2].text)), key=lambda item: item[0])
    height = median(line.bbox["height"] for _, _, line in body)
    margin = height*.8
    financial = [(pos, p, line) for pos, p, line in body if line.bbox["left"] >= value_left
                 and re.search(r"\d", line.text)]
    fragments = preceding_page_amounts(
        anchors, financial, body, row_margin=margin, balance_labels=("SALDO DO DIA",))
    destinations = {(item.origin_page.page_number, item.amount.id): item.target_page.page_number + item.anchor.bbox["top"]
                    for item in fragments}
    # Effective row membership changes; physical page, line and coordinates do not.
    body = [(destinations.get((p.page_number, line.id), pos), p, line) for pos, p, line in body]
    financial = [(destinations.get((p.page_number, line.id), pos), p, line) for pos, p, line in financial]
    used, rows, opening, controls = set(), [], None, {}
    non_cash = 0
    opening_position, row_positions = None, []
    days_with_rows = set()
    for index, (pos, page, anchor) in enumerate(anchors):
        stop = anchors[index+1][0] if index+1 < len(anchors) else end_pos
        band = [(y, p, line) for y, p, line in body if pos-margin <= y < stop-margin]
        values = [(p, line) for y, p, line in financial if pos-margin <= y < stop-margin]
        if len(values) != 1 or not (match := _VALUE.fullmatch(upper(values[0][1].text))):
            fail("Uma linha SISBR está sem seu valor legível.")
        value_page, value_line = values[0]
        signs = [line.text for _, _, line in band if line.bbox["left"] >= value_left and line.text in {"C", "D", "*"}]
        sign = match[2]
        if (sign and signs) or (not sign and len(signs) != 1):
            fail("Um valor SISBR está sem indicador C/D inequívoco.")
        sign = sign or signs[0]
        used.add((value_page.page_number, value_line.id))
        year = int(end[:4]) - (int(anchor.text[3:]) > int(end[5:7]))
        date = day(anchor.text + f"/{year}")
        history = [line for _, _, line in band if columns["HISTORICO"]-.02 <= line.bbox["left"] < value_left]
        if not history:
            fail()
        label = upper(history[0].text)
        if label in {"SALDO BLOQ.ANTERIOR", "SALDO BLOQUEADO ANTERIOR"}:
            if sign != "*":
                fail()
            continue
        if sign == "*" and is_blocked_cheque_history(label):
            if not start <= date <= end:
                fail("Um depósito pendente SISBR está fora do período confirmado.")
            non_cash += 1
            continue
        if sign not in {"C", "D"}:
            fail("Um movimento SISBR está sem sinal C/D explícito.")
        value = money(match[1]) * (-1 if sign == "D" else 1)
        if label == "SALDO ANTERIOR":
            if opening is not None or date >= start:
                fail()
            opening, opening_position = value, pos
        elif label == "SALDO DO DIA":
            controls.setdefault(date, []).append((value, band))
        else:
            if not start <= date <= end:
                fail("Uma data ou a ordem dos movimentos SISBR não está legível.")
            days_with_rows.add(date)
            refs = [re.sub(r"^DOC\.:\s*", "", line.text, flags=re.I) for line in history if upper(line.text).startswith("DOC.:")]
            if len(refs) != 1:
                fail("Uma referência de documento SISBR não pode ser associada.")
            description = " ".join(line.text for line in history if not upper(line.text).startswith("DOC.:"))
            row = _row(page.page_number, anchor.line_index, date, description, value, None, refs[0])
            rows.append(_attach_evidence(row, band))
            row_positions.append(pos)
    if used != {(p.page_number, line.id) for _, p, line in financial} or not rows or opening is None:
        fail("Há um valor SISBR sem movimento ou controle associado.")
    if not days_with_rows.issubset(controls):
        fail("Um dia SISBR está sem seu fechamento.")
    rows = chronological_rows(rows, opening_position=opening_position, first_row_position=row_positions[0])
    validate_idle_day_controls(rows, opening, {date: [value for value, _ in entries] for date, entries in controls.items()})
    running = opening
    for index, row in enumerate(rows):
        running += Decimal(str(row.transaction.amount))
        if index + 1 == len(rows) or rows[index+1].transaction.date != row.transaction.date:
            printed = controls[row.transaction.date]
            if any(value != running for value, _ in printed):
                financial_mismatch()
            values = {value for value, _ in printed}
            rows[index] = replace(row, running_balance=float(next(iter(values))) if len(values) == 1 else None)
            for _, band in printed:
                rows[index] = _attach_evidence(rows[index], band)
    closing_labels = [line for line in last_page.lines if upper(line.text) == "(+) SALDO EM CONTA:"]
    if len(closing_labels) != 1:
        fail()
    closing_values = [line for line in last_page.lines if line.bbox["left"] >= value_left
        and abs(line.bbox["top"] - closing_labels[0].bbox["top"]) < height
        and _VALUE.fullmatch(upper(line.text))]
    if len(closing_values) != 1 or not (match := re.fullmatch(rf"({NUMBER})\s*([CD])", upper(closing_values[0].text))):
        fail("O saldo final SISBR está sem seu sinal C/D legível.")
    closing = money(match[1]) * (-1 if match[2] == "D" else 1)
    if running != closing:
        financial_mismatch()
    return VerifiedStatement(rows, "sicoob_sisbr_native_split_cd_v1", opening, closing,
        printed_balance_checks=len(controls)+1, candidate_rows=len(rows), balance_checkpoints={}, non_cash_entries=non_cash)
