"""Banco do Brasil cash columns from the auto-service print template."""

import re
from dataclasses import replace
from decimal import Decimal
from statistics import median

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "banco_do_brasil_autoatendimento_cd_ocr_v1"
_MONEY = re.compile(rf"({NUMBER}|\d+\.\d{{2}})\s*(?:[Il|]\s+(?=[CD]))?([CD])?", re.IGNORECASE)


def _columns(page):
    keys = ("HISTORICO", "DOCUMENTO", "VALOR R$", "SALDO")
    columns = _headers(page, keys)
    if columns:
        return columns
    others = _headers(page, keys[1:])
    if not others:
        return None
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
        if words and line.bbox["left"] >= columns["DOCUMENTO"] - .07:
            groups = {}
            for word in words:
                x = word.bbox["left"]
                column = "balance" if x >= columns["SALDO"] - .03 else "amount" if x >= columns["VALOR R$"] - .035 else "document"
                groups.setdefault(column, []).append(word)
            if len(groups) > 1:
                for group in groups.values():
                    left = min(word.bbox["left"] for word in group)
                    right = max(word.bbox["left"] + word.bbox["width"] for word in group)
                    result.append(replace(line, text=" ".join(word.text for word in group),
                                          bbox={**line.bbox, "left": left, "width": right-left}))
                continue
        if line.bbox["left"] < columns["HISTORICO"] - .02 < line.bbox["left"] + line.bbox["width"]:
            words = [word for word in words if columns["HISTORICO"] - .02 <= word.bbox["left"] < columns["DOCUMENTO"] - .07]
            if words:
                left = min(word.bbox["left"] for word in words)
                right = max(word.bbox["left"] + word.bbox["width"] for word in words)
                line = replace(line, text=" ".join(word.text for word in words), bbox={**line.bbox, "left": left, "width": right-left})
        result.append(line)
    return result


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
    base_columns = columns
    first_date_x = None
    rows, opening, running, closing, checks = [], None, None, None, 0
    for page in extraction.pages:
        if closing is not None:
            break
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
        lines = _column_lines(page, columns)
        boundary = (columns["VALOR R$"] + columns["SALDO"]) / 2
        for index, anchor in enumerate(dates):
            top = anchor.bbox["top"]
            bottom = dates[index + 1].bbox["top"] - 0.005 if index + 1 < len(dates) else 1
            cells = [item for item in lines if top - 0.005 <= item.bbox["top"] < bottom]
            history = [item for item in cells if columns["HISTORICO"] - 0.02 <= item.bbox["left"] < columns["DOCUMENTO"] - 0.07]
            label = upper(" ".join(item.text for item in history))
            # Textract can emit lot + history as a single WORD as well as a
            # single LINE. These exact control labels have no monetary value
            # or document reference, so recognize the printed marker itself.
            controls = [item for item in cells if abs(item.bbox["top"]-top) < .006
                        and re.fullmatch(r"(?:0{5})?(?:000SALDOANTERIOR|999SALDO)", upper(item.text).replace(" ", ""))]
            if len(controls) == 1:
                label = "SALDO ANTERIOR" if "ANTERIOR" in upper(controls[0].text) else "999 SALDO"
            baseline_label = upper(" ".join(item.text for item in history if abs(item.bbox["top"]-top) < .006)).replace(" ", "")
            if (len(controls) == 1 and label == "999 SALDO") or baseline_label.removeprefix("999") == "SALDO":
                # The final date has no next anchor; limits, CET and footer
                # balances below it belong to another financial section.
                cells = [item for item in cells if abs(item.bbox["top"] - top) < .006]
                history = [item for item in history if item in cells]
                label = "999 SALDO"
            values = [item for item in cells if item.bbox["left"] > columns["DOCUMENTO"] + 0.05 and _MONEY.fullmatch(item.text.strip())]
            amounts = [item for item in values if item.bbox["left"] + item.bbox["width"] / 2 < boundary]
            balances = [item for item in values if item not in amounts]

            def value(cell):
                signed = _MONEY.fullmatch(cell.text.strip())
                amount = money(signed[1]) if "," in signed[1] else Decimal(signed[1])
                if signed[2] is None:
                    signs = [
                        item.text.upper()
                        for item in cells
                        if item.text.upper() in {"C", "D"}
                        and abs(item.bbox["top"] - cell.bbox["top"]) < 0.006
                        and 0 <= item.bbox["left"] - (cell.bbox["left"] + cell.bbox["width"]) < 0.035
                    ]
                    if len(signs) == 1:
                        return amount * (-1 if signs[0] == "D" else 1)
                    if signs:
                        fail()
                    # Both the numeric history code and its explicit received
                    # label must agree; the complete printed ledger still closes.
                    if cell not in amounts or not label.startswith(("821 PIX RECEBIDO", "144 PIX ENVIADO")):
                        fail("Um valor Banco do Brasil está sem indicador C/D legível. Envie uma digitalização mais nítida.")
                    return amount * (-1 if label.startswith("144 PIX ENVIADO") else 1)
                return amount * (-1 if signed[2].upper() == "D" else 1)

            if "SALDO ANTERIOR" in label:
                if rows or opening is not None or amounts or len(balances) != 1:
                    fail()
                opening = running = value(balances[0])
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
            if balance is not None:
                if balance != running:
                    fail("Os valores do Banco do Brasil não fecham com o saldo impresso. Confira o documento original.")
                checks += 1
            refs = [
                item
                for item in cells
                if columns["DOCUMENTO"] - 0.07 <= item.bbox["left"] < columns["VALOR R$"] - 0.03
                and abs(item.bbox["top"] - top) < 0.006
                and item not in values
                and re.fullmatch(r"[\d.,]+", item.text)
            ]
            if len(refs) != 1:
                fail()
            description = re.sub(r"^\d{3}\s+", "", " ".join(item.text for item in history))
            row = _source(
                _row(page.page_number, anchor.line_index, day(anchor.text), description, amount, balance, refs[0].text), page, cells
            )
            if _MONEY.fullmatch(amounts[0].text.strip())[2] is None or unreadable_balance_sign:
                row = replace(row, warnings=[*row.warnings, "manual_review_recommended", "amount_sign_from_explicit_pix_label"])
            rows.append(row)
    if not rows or closing is None or not checks or any(a.transaction.date > b.transaction.date for a, b in zip(rows, rows[1:])):
        fail()
    return VerifiedStatement(rows, LAYOUT, opening, closing, printed_balance_checks=checks)
