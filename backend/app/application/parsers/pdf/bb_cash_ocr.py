"""Banco do Brasil cash columns from the auto-service print template."""

import re
from dataclasses import replace
from decimal import Decimal
from statistics import median

from app.application.normalization.ocr_numeric_separators import normalize_ocr_numeric_separators
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.bb_cash_controls import is_pending_deposit
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.page_financial_fragments import preceding_page_amounts
from app.application.parsers.pdf.row_geometry import local_row_slopes, nearest_row, row_slope
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "banco_do_brasil_autoatendimento_cd_ocr_v1"
_MONEY = re.compile(rf"({NUMBER}|\d+\.\d{{2}})\s*(?:[Il|1]\s*(?=[CD]|$))?([CD]|(?<=\s)0)?(?:\s+&)?", re.IGNORECASE)
_PENDING_AMOUNT = re.compile(rf"{NUMBER}\s*\*")


def _duplicated_sign_stroke(page, cell):
    """A low-confidence trailing 1 overlaps an independently printed C/D word.

    This does not authorize rounding a three-decimal amount. The reader also
    requires an independent printed running balance before accepting a row.
    """
    words = [word for word in page.words if word.id in cell.word_ids]
    if len(words) != 2 or any(not word.bbox or word.text_type != "PRINTED" for word in words):
        return False
    numbers = [word for word in words if re.fullmatch(rf"{NUMBER}1", word.text)]
    signs = [word for word in words if word.text.upper() in {"C", "D"}]
    if len(numbers) != 1 or len(signs) != 1:
        return False
    number, sign = numbers[0], signs[0]
    return (number.confidence is not None and number.confidence < 85
            and sign.confidence is not None and sign.confidence >= 90
            and cell.text.replace(" ", "").upper() == number.text + sign.text.upper()
            and abs(number.bbox["top"] - sign.bbox["top"]) < .006
            and 0 < number.bbox["left"] + number.bbox["width"] - sign.bbox["left"] <= .006)


def _columns(page):
    keys = ("HISTORICO", "DOCUMENTO", "VALOR R$", "SALDO")
    columns = _headers(page, keys)
    if columns:
        return columns
    from app.application.parsers.pdf.native_table_source import native_header_columns

    optional = native_header_columns(page, ("HISTORICO", "VALOR R$", "SALDO"))
    if optional and not any(upper(item.text) == "DOCUMENTO" for item in [*page.lines, *page.words]):
        return optional
    others = _headers(page, keys[1:])
    if not others:
        # Native exports can join several headings into one LINE; WORD
        # positions still identify each printed column on the same baseline.
        documents = [word for word in page.words if word.bbox and upper(word.text) == "DOCUMENTO"]
        if len(documents) != 1:
            return None
        baseline = documents[0].bbox["top"]
        result = {}
        for key in keys:
            candidates = [word for word in page.words if word.bbox and upper(word.text) == key.split()[0]
                          and abs(word.bbox["top"] - baseline) < .006]
            if len(candidates) != 1:
                return None
            result[key] = candidates[0].bbox["left"]
        if list(result.values()) != sorted(result.values()):
            return None
        return result
    headers = [line for line in page.lines if line.bbox and upper(line.text) == "DOCUMENTO"]
    candidates = [word for word in page.words if word.bbox and upper(word.text) == "HISTORICO"
                  and len(headers) == 1 and abs(word.bbox["top"] - headers[0].bbox["top"]) < .006
                  and word.bbox["left"] < others["DOCUMENTO"]]
    if len(candidates) == 1:
        return {**others, "HISTORICO": candidates[0].bbox["left"]}
    return None


def _column_lines(page, columns):
    """Separate merged column cells using their words, retaining source line IDs."""
    result = []
    for line in page.lines:
        if not line.bbox:
            continue
        words = sorted([word for word in page.words if word.id in line.word_ids and word.bbox],
                       key=lambda word: word.bbox["left"])
        if words and (line.bbox["left"] >= columns["DOCUMENTO"] - .07
                      or any(word.bbox["left"] >= columns["VALOR R$"] - .035 for word in words)):
            groups = {}
            previous_word = previous_column = None
            for word in words:
                x = word.bbox["left"]
                column = ("balance" if x >= columns["SALDO"] - .03 else "amount" if x >= columns["VALOR R$"] - .035
                          else "document" if x >= columns["DOCUMENTO"] - .07 else "history")
                if (word.text.upper() in {"C", "D"} and previous_word is not None
                        and _MONEY.fullmatch(previous_word.text.strip())
                        and 0 <= x - (previous_word.bbox["left"] + previous_word.bbox["width"]) < .025):
                    column = previous_column
                if (word.text == "*" and previous_column == "amount" and previous_word is not None
                        and re.fullmatch(NUMBER, previous_word.text.strip())
                        and abs(word.bbox["top"] - previous_word.bbox["top"]) < .006
                        and 0 <= x - (previous_word.bbox["left"] + previous_word.bbox["width"]) < .025):
                    column = "amount"
                groups.setdefault(column, []).append(word)
                previous_word, previous_column = word, column
            if len(groups) > 1:
                for role, group in groups.items():
                    if group[0].bbox["left"] < columns["HISTORICO"] - .02:
                        group = [word for word in group if word.bbox["left"] >= columns["HISTORICO"] - .02]
                    if not group:
                        continue
                    left = min(word.bbox["left"] for word in group)
                    right = max(word.bbox["left"] + word.bbox["width"] for word in group)
                    result.append(replace(line, id=line.id + ":" + role, text=" ".join(word.text for word in group),
                                          bbox={**line.bbox, "left": left, "width": right-left},
                                          word_ids=tuple(word.id for word in group)))
                continue
        if line.bbox["left"] < columns["HISTORICO"] - .02 < line.bbox["left"] + line.bbox["width"]:
            words = [word for word in words if columns["HISTORICO"] - .02 <= word.bbox["left"] < columns["DOCUMENTO"] - .07]
            if words:
                left = min(word.bbox["left"] for word in words)
                right = max(word.bbox["left"] + word.bbox["width"] for word in words)
                line = replace(line, text=" ".join(word.text for word in words), bbox={**line.bbox, "left": left, "width": right-left})
        result.append(line)
    normalized = []
    for line in result:
        pair = re.fullmatch(rf"({NUMBER}\s*[CD*])\s+({NUMBER}\s*[CD]?)", line.text.strip(), re.I)
        if pair:
            if (line.bbox["left"] > columns["VALOR R$"] + .06
                    or line.bbox["left"] + line.bbox["width"] < columns["SALDO"] - .03):
                fail("A célula BB mesclada não cobre as duas colunas financeiras.")
            normalized.extend(replace(line, id=line.id + ":" + role, text=value)
                              for role, value in (("amount", pair[1]), ("balance", pair[2])))
        else:
            normalized.append(line)
    return _attach_pending_markers(normalized, columns)


def _attach_pending_markers(lines, columns):
    """A detached printed star belongs only to one adjacent unsigned amount.

    This restores geometry, not financial meaning: the row must still have an
    explicit blocked-deposit description before it may be excluded from cash.
    """
    attached, markers = {}, set()
    for marker in lines:
        if marker.text.strip() != "*" or marker.bbox["left"] < columns["VALOR R$"] - .035:
            continue
        candidates = [cell for cell in lines if re.fullmatch(NUMBER, cell.text.strip())
                      and cell.bbox["left"] >= columns["VALOR R$"] - .035
                      and (cell.id.endswith(":amount")
                           or cell.bbox["left"] + cell.bbox["width"] < columns["SALDO"] - .005)
                      and abs(marker.bbox["top"] - cell.bbox["top"]) < .006
                      and 0 <= marker.bbox["left"] - (cell.bbox["left"] + cell.bbox["width"]) < .025]
        if not candidates:
            continue
        if len(candidates) != 1 or candidates[0].id in attached:
            fail("O asterisco BB não pode ser associado a um único valor de depósito.")
        cell = candidates[0]
        right = marker.bbox["left"] + marker.bbox["width"]
        attached[cell.id] = replace(cell, text=cell.text + " *",
            bbox={**cell.bbox, "width": right - cell.bbox["left"]},
            word_ids=(*cell.word_ids, *marker.word_ids))
        markers.add(marker.id)
    return [attached.get(line.id, line) for line in lines if line.id not in markers]


def is_bb_cash_ocr(text):
    normalized = upper(text)
    return (
        "CLIENTE CONTA ATUAL" in normalized.replace(" - ", " ")
        and "BALANCETE" in normalized
        and "AG. ORIGEM" in normalized
        and "VALOR R$" in normalized
        and "SALDO" in normalized
    )


def parse_bb_cash_ocr(extraction):
    columns = _columns(extraction.pages[0])
    if not columns:
        fail()
    has_document = "DOCUMENTO" in columns
    if not has_document:
        # A zero-width reference area lets history extend to the amount column.
        columns["DOCUMENTO"] = columns["VALOR R$"] + .04
    base_columns = columns
    base_slope = row_slope([item for item in extraction.pages[0].lines if item.bbox and upper(item.text) in columns])
    first_date_x = None
    first_history_x = None
    frames = []
    page_slopes = {}
    separator_repairs = set()
    for page in extraction.pages:
        lines = [line for line in page.lines if line.bbox]
        dates = sorted(
            [item for item in lines if item.bbox["left"] < 0.17 and re.fullmatch(DATE, item.text)], key=lambda item: item.bbox["top"]
        )
        if not dates:
            fail()
        date_x = median(item.bbox["left"] for item in dates)
        if first_date_x is None:
            first_date_x = date_x
        offset = date_x - first_date_x
        if abs(offset) > 0.08 or any(abs(item.bbox["left"] - date_x) > 0.02 for item in dates):
            fail()
        columns = {key: value + offset for key, value in base_columns.items()}
        if not has_document:
            histories = [item.bbox["left"] for item in lines if re.match(r"^\d{3}\s+[A-Za-zÀ-ÿ]", item.text)
                         and .2 < item.bbox["left"] < .6]
            if not histories:
                fail("A coluna de histórico Banco do Brasil está ausente.")
            history_x = median(histories)
            if first_history_x is None:
                first_history_x = history_x
            scale = (history_x-date_x)/(first_history_x-first_date_x)
            if not .8 <= scale <= 1.2:
                fail("As colunas do extrato Banco do Brasil não mantêm uma escala identificável.")
            columns = {key: date_x+(value-first_date_x)*scale for key, value in base_columns.items()}
        lines = _column_lines(page, columns)
        if extraction.provider == "aws_textract":
            repaired = []
            for item in lines:
                split_decimal = re.fullmatch(r"(\d+)\s+(\d{2})\s+([CD])", item.text, re.I)
                if split_decimal and item.bbox["left"] >= columns["VALOR R$"]-.035:
                    separator_repairs.add((page.page_number, item.id))
                    item = replace(item, text=f"{split_decimal[1]},{split_decimal[2]} {split_decimal[3]}")
                repaired.append(item)
            lines = repaired
            lines = [replace(item, text=normalize_ocr_numeric_separators(item.text))
                     if item.bbox["left"] >= columns["VALOR R$"]-.035 else item for item in lines]
            lines = [replace(item, text=re.sub(r"^(\d{1,3}(?:,\d{3})+)\.(\d{2})(\s*[CD])$",
                     lambda match: match[1].replace(",", ".")+","+match[2]+match[3], item.text))
                     if item.bbox["left"] >= columns["VALOR R$"]-.035 else item for item in lines]
        headings = [item for item in lines if upper(item.text) in columns]
        slope = row_slope(headings) if len(headings) >= 2 else base_slope
        if not has_document:
            descriptions = [item for item in lines if columns["HISTORICO"]-.02 <= item.bbox["left"] < columns["VALOR R$"]-.035
                            and re.match(r"^\d{3}\s+[A-Za-zÀ-ÿ]", item.text)]
            slopes = local_row_slopes(dates, descriptions, fallback=slope)
            page_slopes[page.page_number] = slopes
            lines = [replace(item, text=re.sub(r"^(\d{1,3}(?:\.\d{3})+)\.(\d{2})(\s*[CD])$", r"\1,\2\3", item.text))
                     if item.bbox["left"] >= columns["VALOR R$"]-.035 else item for item in lines]
        financial_left = columns["DOCUMENTO"] + .05 if has_document else columns["VALOR R$"]-.035
        financial = [item for item in lines if item.bbox["left"] > financial_left
                     and (_MONEY.fullmatch(item.text.strip()) or _PENDING_AMOUNT.fullmatch(item.text.strip()))]
        frames.append((page, columns, dates, lines, slope, financial))
        if any((re.fullmatch(r"(?:0{5})?999SALDO", upper(item.text).replace(" ", ""))
                or upper(item.text).replace(" ", "") == "SALDO"
                and columns["HISTORICO"] - .02 <= item.bbox["left"] < columns["DOCUMENTO"] - .07
                and any(abs(item.bbox["top"] - anchor.bbox["top"]) < .006 for anchor in dates)) for item in lines):
            break
    amount_cells = [(page.page_number + item.bbox["top"], page, item)
                    for page, columns, _, _, _, financial in frames for item in financial
                    if item.id.endswith(":amount") or not item.id.endswith(":balance")
                    and item.bbox["left"] + item.bbox["width"] < columns["SALDO"] - .005]
    fragments = preceding_page_amounts(
        [(page.page_number + item.bbox["top"], page, item) for page, _, dates, _, _, _ in frames for item in dates],
        amount_cells,
        [(page.page_number + item.bbox["top"], page, item) for page, _, _, lines, _, _ in frames for item in lines])
    rows, opening, running, closing, checks = [], None, None, None, 0
    non_cash = 0
    for page, columns, dates, lines, slope, financial in frames:
        slopes = page_slopes.get(page.page_number)

        def row_position(item):
            if not slopes:
                return item.bbox["top"]
            # This selects a local deskew estimate for text windows, not a
            # financial row. Actual monetary association retains tie rejection.
            nearest = min(dates, key=lambda anchor: abs(anchor.bbox["top"]-item.bbox["top"]))
            return (item.bbox["top"] - slopes[nearest.id]*(item.bbox["left"]-nearest.bbox["left"])
                    if slopes and nearest is not None else item.bbox["top"])
        if closing is not None:
            break
        outgoing = {item.amount.id for item in fragments if item.origin_page.page_number == page.page_number}
        incoming = [item for item in fragments if item.target_page.page_number == page.page_number]
        lines = [item for item in lines if item.id not in outgoing]
        financial = [item for item in financial if item.id not in outgoing]
        assigned = {item.id: nearest_row(item, dates, slope=slope, slopes=slopes) for item in financial}
        for fragment in incoming:
            financial.append(fragment.amount)
            assigned[fragment.amount.id] = fragment.anchor
        if rows and dates[0].bbox["top"] < .15:
            first_y = dates[0].bbox["top"]
            continuation = [line for line in lines if columns["HISTORICO"]-.02 <= line.bbox["left"] < columns["DOCUMENTO"]-.07
                            and first_y-.05 <= line.bbox["top"] < first_y-.006]
            if continuation:
                previous = rows[-1]
                extra = _source(previous, page, continuation)
                rows[-1] = replace(previous, transaction=replace(previous.transaction,
                    description=previous.transaction.description + " " + " ".join(line.text for line in continuation)),
                    source_regions=(*previous.source_regions, *extra.source_regions),
                    source_line_refs=(*previous.source_line_refs, *extra.source_line_refs))
        for index, anchor in enumerate(dates):
            top = anchor.bbox["top"]
            bottom = dates[index + 1].bbox["top"] - 0.005 if index + 1 < len(dates) else 1
            lower = top - .005
            native = extraction.provider == "native_pdfium_text"
            cells = [item for item in lines if lower <= row_position(item) < bottom]
            history = [item for item in cells if columns["HISTORICO"] - 0.02 <= item.bbox["left"] < columns["DOCUMENTO"] - 0.07]
            label = upper(" ".join(item.text for item in history))
            # Textract can emit lot + history as a single WORD as well as a
            # single LINE. These exact control labels have no monetary value
            # or document reference, so recognize the printed marker itself.
            controls = [item for item in cells if abs(row_position(item)-top) < .006
                        and re.fullmatch(r"(?:0{5})?(?:000SALDOANTERIOR|999SALDO)", upper(item.text).replace(" ", ""))]
            if len(controls) == 1:
                label = "SALDO ANTERIOR" if "ANTERIOR" in upper(controls[0].text) else "999 SALDO"
            baseline_label = upper(" ".join(item.text for item in history if abs(row_position(item)-top) < .006)).replace(" ", "")
            if (len(controls) == 1 and label == "999 SALDO") or baseline_label.removeprefix("999") == "SALDO":
                # The final date has no next anchor; limits, CET and footer
                # balances below it belong to another financial section.
                cells = [item for item in lines if nearest_row(item, [anchor], slope=slope, slopes=slopes,
                         max_distance=.018 if native else .006) is anchor]
                history = [item for item in history if item in cells]
                label = "999 SALDO"
            values = [item for item in financial if assigned[item.id] is anchor]
            if label == "999 SALDO":
                values = [item for item in values if item in cells]
            signs = [item for item in lines if item.text.upper() in {"C", "D"}
                     and item.bbox["left"] > (columns["DOCUMENTO"] + .05 if has_document else columns["VALOR R$"]-.035)
                     and nearest_row(item, dates, slope=slope, slopes=slopes) is anchor]
            cells = list({item.id:item for item in [*cells,*values,*signs]}.values())
            amounts = [item for item in values if item.id.endswith(":amount") or not item.id.endswith(":balance")
                       and item.bbox["left"] + item.bbox["width"] < columns["SALDO"] - .005]
            balances = [item for item in values if item not in amounts]

            inferred_signs = set()
            recovered_strokes = set()
            recovered_marks = set()

            def value(cell):
                signed = _MONEY.fullmatch(cell.text.strip())
                amount = money(signed[1]) if "," in signed[1] else Decimal(signed[1])
                if cell.text.strip().endswith("&"):
                    if (extraction.provider != "aws_textract" or cell not in amounts or signed[2] is None
                            or cell.confidence is None or cell.confidence >= 85):
                        fail("A marca adicional junto ao valor BB não está confirmada.")
                    recovered_marks.add(cell.id)
                if re.fullmatch(rf"{re.escape(signed[1])}\s*1\s*[CD]?(?:\s+&)?", cell.text.strip(), re.I):
                    if extraction.provider != "aws_textract" or cell not in amounts or not _duplicated_sign_stroke(page, cell):
                        fail("O dígito adicional junto ao valor BB não tem confirmação independente no documento.")
                    recovered_strokes.add(cell.id)
                if signed[2] not in {"C", "D", "c", "d"}:
                    signs = [
                        item.text.upper()
                        for item in cells
                        if item.text.upper() in {"C", "D"}
                        and (any(fragment.amount is cell for fragment in incoming)
                             or abs(item.bbox["top"] - cell.bbox["top"]) < (0.018 if extraction.provider == "native_pdfium_text" else .006))
                        and -0.012 <= item.bbox["left"] - (cell.bbox["left"] + cell.bbox["width"]) < 0.035
                        and not any(other is not cell
                                    and abs(item.bbox["left"] - (other.bbox["left"] + other.bbox["width"]))
                                    <= abs(item.bbox["left"] - (cell.bbox["left"] + cell.bbox["width"]))
                                    for other in values if (other_signed := _MONEY.fullmatch(other.text.strip()))
                                    and other_signed[2] is None)
                    ]
                    if len(signs) == 1:
                        return amount * (-1 if signs[0] == "D" else 1)
                    if signs:
                        fail()
                    if re.search(r"[Il|]\s*$", cell.text):
                        fail("A marca junto ao valor Banco do Brasil está sem indicador C/D independente.")
                    # Both the numeric history code and its explicit received
                    # label must agree; the complete printed ledger still closes.
                    explicit_direction = ("821 PIX RECEBIDO", "144 PIX ENVIADO", "102 CHEQUE COMPENSADO",
                                          "429 TAR PROCESSAMENTO CHEQUE")
                    if cell not in amounts or not label.startswith(explicit_direction):
                        fail("Um valor Banco do Brasil está sem indicador C/D legível. Envie uma digitalização mais nítida.")
                    inferred_signs.add(cell.id)
                    return amount * (1 if label.startswith("821 PIX RECEBIDO") else -1)
                return amount * (-1 if signed[2].upper() == "D" else 1)

            pending = [cell for cell in amounts if _PENDING_AMOUNT.fullmatch(cell.text.strip())]
            if pending:
                if (len(amounts) != 1 or len(pending) != 1 or len(balances) > 1
                        or any("*" in item.text for item in cells if item not in pending
                               and item.bbox["left"] > columns["DOCUMENTO"] + .05)
                        or not is_pending_deposit(label + " " + pending[0].text)):
                    fail("Um valor bloqueado BB está sem identificação inequívoca de depósito pendente.")
                if running is None:
                    fail("O depósito pendente BB está sem contexto de saldo anterior.")
                if balances:
                    if value(balances[0]) != running:
                        financial_mismatch("O saldo impresso junto ao depósito pendente BB diverge do caixa disponível.")
                    checks += 1
                non_cash += 1
                continue
            if "SALDO ANTERIOR" in label:
                if (rows or opening is not None or not re.fullmatch(r"(?:\d+\s+)?SALDO ANTERIOR", label)
                        or not values or len(amounts) > 1 or len(balances) > 1):
                    fail()
                copies = {value(cell) for cell in values}
                if len(copies) != 1:
                    fail("As cópias impressas do saldo anterior Banco do Brasil divergem.")
                opening = running = copies.pop()
                continue
            if label.replace(" ", "").removeprefix("999") == "SALDO":
                if amounts or len(balances) != 1 or running != value(balances[0]):
                    fail()
                closing = running
                checks += 1
                break
            if opening is None or len(amounts) != 1 or len(balances) > 1 or not history:
                fail()
            amount = value(amounts[0])
            running += amount
            unreadable_balance_sign = bool(
                balances
                and _MONEY.fullmatch(balances[0].text.strip())[2] is None
                and not any(item.text.upper() in {"C", "D"} for item in cells)
            )
            if unreadable_balance_sign:
                token = _MONEY.fullmatch(balances[0].text.strip())[1]
                magnitude = money(token) if "," in token else Decimal(token)
                if abs(running) != magnitude:
                    fail()
            balance = value(balances[0]) if balances and not unreadable_balance_sign else None
            if (recovered_strokes or recovered_marks) and (balance is None or balance != running):
                fail("A recuperação do caractere BB não foi confirmada pelo saldo impresso da linha.")
            if balance is not None:
                if balance != running:
                    financial_mismatch("Os valores do Banco do Brasil não fecham com o saldo impresso. Confira o documento original.")
                checks += 1
            refs = [
                item
                for item in lines
                if columns["DOCUMENTO"] - 0.07 <= item.bbox["left"] < columns["VALOR R$"] - 0.03
                and nearest_row(item, dates, slope=slope) is anchor
                and item not in values
                and re.fullmatch(r"[\d.,]+", item.text)
            ]
            if has_document and len(refs) != 1:
                fail()
            if not has_document:
                refs = []
            cells = list({item.id: item for item in [*cells, *refs]}.values())
            description = re.sub(r"^\d{3}\s+", "", " ".join(item.text for item in history))
            movement_dates = [item for item in cells if .17 < item.bbox["left"] < .29
                              and re.fullmatch(DATE, item.text) and abs(item.bbox["top"] - top) < .008]
            if len(movement_dates) > 1:
                fail()
            date = movement_dates[0].text if movement_dates else anchor.text
            carried = [item for item in incoming if item.anchor is anchor]
            reference = refs[0].text if refs else None
            row = _source(_row(page.page_number, anchor.line_index, day(date), description, amount, balance, reference),
                          page, [cell for cell in cells if not any(item.amount is cell for item in carried)])
            for fragment in carried:
                evidence = _source(row, fragment.origin_page, [fragment.amount])
                row = replace(row, source_regions=(*row.source_regions, *evidence.source_regions),
                              source_line_refs=(*row.source_line_refs, *evidence.source_line_refs))
            if amounts[0].id in inferred_signs or unreadable_balance_sign:
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended", "amount_sign_from_explicit_pix_label"])
            if recovered_strokes:
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended", "ocr_sign_stroke_overlap_confirmed"])
            if recovered_marks:
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended", "ocr_trailing_mark_confirmed"])
            if any((page.page_number, item.id) in separator_repairs for item in values):
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended", "ocr_decimal_separator_recovered"])
            rows.append(row)
    from app.application.parsers.pdf.statement_completeness import validate_statement_completeness

    partial = validate_statement_completeness(["\n".join(line.text for line in page.lines) for page in extraction.pages])
    if (not rows or closing is None and not partial or not checks
            or any(a.transaction.date > b.transaction.date for a, b in zip(rows, rows[1:]))):
        fail()
    return VerifiedStatement(rows, LAYOUT, opening, closing, printed_balance_checks=checks, non_cash_entries=non_cash,
                             partial=partial, balance_bounds_unavailable=partial)
