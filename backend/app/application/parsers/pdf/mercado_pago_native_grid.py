"""Preserve Mercado Pago histories using the actual printed row borders."""
import re
from contextlib import closing
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.native_table_source import split_native_columns
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK
from app.application.parsers.pdf.sweep_native_statements import _mercado_pago
from app.application.parsers.pdf.text_extraction import read_layout_native_pdf_page_texts
from app.application.parsers.pdf.verified_statements import NUMBER, day, fail, money
from app.application.sweep_statement_detection import MERCADO_PAGO

_CURRENCY = re.compile(rf"R\$\s*([+-]?{NUMBER})")


def _split_date_history(page, history_left):
    words = {word.id: word for word in page.words}
    lines = []
    for line in page.lines:
        if line.bbox["left"] < history_left and re.match(r"^\d{2}-\d{2}-\d{4}\s+", line.text):
            cells = [words[key] for key in line.word_ids]
            groups = ([word for word in cells if word.bbox["left"] < history_left - .02],
                      [word for word in cells if word.bbox["left"] >= history_left - .02])
            if not all(groups) or not re.fullmatch(r"\d{2}-\d{2}-\d{4}", " ".join(word.text for word in groups[0])):
                fail()
            for group in groups:
                left = min(word.bbox["left"] for word in group)
                right = max(word.bbox["left"] + word.bbox["width"] for word in group)
                lines.append(replace(line, text=" ".join(word.text for word in group),
                    bbox={**line.bbox, "left": left, "width": right-left}, word_ids=tuple(word.id for word in group)))
        else:
            lines.append(line)
    return replace(page, lines=lines)


def parse_mercado_pago_native_grid(raw, source):
    import pypdfium2 as pdfium

    recovered, pending, previous_columns = [], [], None
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as document:
        for index, page in enumerate(source.pages):
            columns = _headers(page, ("DATA", "DESCRICAO", "ID DA OPERACAO", "VALOR", "SALDO"))
            has_header = bool(columns)
            if not columns:
                if index == 0:
                    return None
                columns = previous_columns
            previous_columns = columns
            page = _split_date_history(page, columns["DESCRICAO"])
            page = split_native_columns(page, [columns["DESCRICAO"] - .02, columns["ID DA OPERACAO"] - .02])
            header_top = max((line.bbox["top"] for line in page.lines
                             if normalize_upper_text(line.text) in {"DATA", "DESCRICAO", "VALOR", "SALDO"}),
                             default=0) if has_header else 0
            with closing(document[index]) as pdf_page:
                width, height = pdf_page.get_size()
                rules = set()
                for obj in pdf_page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_PATH]):
                    left, bottom, right, top = obj.get_bounds()
                    y = 1 - (top + bottom) / (2 * height)
                    if (left / width <= columns["DESCRICAO"] and right / width >= columns["ID DA OPERACAO"] - .025
                            and (top - bottom) / height < .004 and y > header_top + .005):
                        rules.add(round(y, 4))
            rules = sorted(rules)
            if not rules and index == 0:
                return None
            dates = [line for line in page.lines if line.bbox["left"] < columns["DESCRICAO"] - .03
                     and re.fullmatch(r"\d{2}-\d{2}-\d{4}", line.text)]
            if len(rules) < 2 or not dates:
                fail("Não foi possível associar todas as operações às linhas impressas do Mercado Pago.")
            used, used_dates = set(), set()
            for band, (lower, upper) in enumerate(zip(rules, rules[1:])):
                cells = sorted([line for line in page.lines if lower < line.bbox["top"] + line.bbox["height"] / 2 < upper],
                               key=lambda line: (line.bbox["top"], line.bbox["left"]))
                anchors = [line for line in dates if line in cells]
                currencies = sorted([line for line in cells if _CURRENCY.fullmatch(line.text)], key=lambda line: line.bbox["left"])
                refs = [line for line in cells if columns["ID DA OPERACAO"] - .02 <= line.bbox["left"] < columns["VALOR"] - .03
                        and re.fullmatch(r"\d+", line.text)]
                history = [line for line in cells if columns["DESCRICAO"] - .025 <= line.bbox["left"] < columns["ID DA OPERACAO"] - .02]
                if not anchors and not currencies and not refs and history:
                    if band == len(rules) - 2 and index + 1 < len(source.pages):
                        pending.append((page, history))
                        continue
                    if band == 0 and recovered and not pending:
                        previous = recovered[-1]
                        recovered[-1] = (*previous[:3], previous[3] + " " + " ".join(line.text for line in history),
                                         previous[4], previous[5], [*previous[6], (page, history)])
                        continue
                    fail()
                if len(anchors) != 1 or len(currencies) != 2 or len(refs) != 1 or not history:
                    fail()
                used.update(line.id for line in currencies)
                used_dates.add(anchors[0].id)
                values = [money(_CURRENCY.fullmatch(line.text)[1]) for line in currencies]
                description = " ".join([*(line.text for _, group in pending for line in group), *(line.text for line in history)])
                recovered.append((anchors[0], refs[0].text, values, description, page, cells, pending))
                pending = []
            all_cash = {line.id for line in page.lines if line.bbox["top"] > header_top + .005 and _CURRENCY.fullmatch(line.text)}
            if used != all_cash or used_dates != {line.id for line in dates}:
                fail()
    if pending:
        fail()
    statement = _mercado_pago(read_layout_native_pdf_page_texts(raw), MERCADO_PAGO)
    if len(statement.rows) != len(recovered):
        fail()
    rows = []
    for row, (anchor, reference, values, description, page, cells, continuation) in zip(statement.rows, recovered):
        if (row.transaction.date != day(anchor.text, "%d-%m-%Y") or row.external_reference_id != reference
                or Decimal(str(row.transaction.amount)) != values[0] or Decimal(str(row.running_balance)) != values[1]):
            fail()
        recovered_row = _source(replace(row, transaction=replace(row.transaction, description=description)), page, cells)
        for extra_page, extra_cells in continuation:
            evidence = _source(recovered_row, extra_page, extra_cells)
            recovered_row = replace(recovered_row, source_regions=(*recovered_row.source_regions, *evidence.source_regions),
                source_line_refs=(*recovered_row.source_line_refs, *evidence.source_line_refs))
        rows.append(recovered_row)
    return replace(statement, rows=rows)
