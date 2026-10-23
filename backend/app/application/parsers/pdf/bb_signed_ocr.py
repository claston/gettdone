"""BB's five-column signed ledger, including vertically centered OCR cells."""

import re
from dataclasses import replace
from decimal import Decimal

from app.application.errors import OcrFinancialAmbiguityError
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, day, fail, money

LAYOUT = "banco_do_brasil_extrato_conta_corrente_lancamentos_v1"
SIGNED = re.compile(rf"({NUMBER})\s*(?:\(\s*)+([+-])\s*\)")
MESSAGE = "Não foi possível separar uma data, um valor ou seu sinal no extrato Banco do Brasil. Confira o documento original."


def parse_bb_signed_ocr(extraction):
    joined = upper(" ".join(line.text for page in extraction.pages for line in page.lines))
    if "EXTRATO DE CONTA CORRENTE" not in joined or not re.search(r"\([+-]\)", joined):
        return None
    columns = _headers(extraction.pages[0], ("DIA", "LOTE", "DOCUMENTO", "HISTORICO", "VALOR"))
    if not columns:
        return None
    years = set(re.findall(r"\b\d{2}/\d{2}/(20\d{2})\b", joined))
    opening = running = closing = current = None
    checks = 0
    rows = []
    base = columns
    for page in extraction.pages:
        if closing is not None:
            break
        columns = _headers(page, ("DIA", "LOTE", "DOCUMENTO", "HISTORICO", "VALOR")) or base
        lines = sorted((line for line in page.lines if line.bbox), key=lambda line: (line.bbox["top"], line.bbox["left"]))
        header = [line.bbox["top"] for line in lines if upper(line.text) in {"DIA", "HISTORICO", "VALOR"}]
        top = max(header, default=0)
        value_left = (columns["HISTORICO"] + columns["VALOR"]) / 2
        amounts = [line for line in lines if line.bbox["top"] > top + .005
                   and line.bbox["left"] >= value_left and SIGNED.fullmatch(line.text.strip())]
        dates = [line for line in lines if line.bbox["top"] > top + .005
                 and line.bbox["left"] < columns["LOTE"] - .02
                 and re.fullmatch(r"\d{2}/\d{2}/(?:\d{4}|20\d)", line.text.strip())]
        used_dates = set()
        if not amounts:
            raise OcrFinancialAmbiguityError(MESSAGE)
        for index, value in enumerate(amounts):
            y = value.bbox["top"]
            lower = (amounts[index - 1].bbox["top"] + y) / 2 if index else top + .005
            upper_y = (amounts[index + 1].bbox["top"] + y) / 2 if index + 1 < len(amounts) else min(1, y + .025)
            cells = [line for line in lines if lower <= line.bbox["top"] < upper_y]
            anchors = [line for line in dates if line in cells]
            if len(anchors) != 1:
                raise OcrFinancialAmbiguityError(MESSAGE)
            anchor = anchors[0]
            used_dates.add(anchor.id)
            history = [line for line in cells if columns["HISTORICO"] - .02 <= line.bbox["left"] < value_left]
            label = " ".join(line.text.strip() for line in history)
            compact = upper(label).replace(" ", "")
            matched = SIGNED.fullmatch(value.text.strip())
            amount = money(matched[1]) * (-1 if matched[2] == "-" else 1)
            if any(word.id in value.word_ids and word.text_type == "HANDWRITING" for word in page.words):
                raise OcrFinancialAmbiguityError(MESSAGE)
            is_control = "SALDOANTERIOR" in compact or "SALDODODIA" in compact or compact in {"SALDO", "SALDOATUAL"}
            date_text = anchor.text.strip()
            if re.fullmatch(r"\d{2}/\d{2}/20\d", date_text):
                if len(years) != 1 or not next(iter(years)).startswith(date_text[-3:]):
                    raise OcrFinancialAmbiguityError(MESSAGE)
                date_text = date_text[:6] + next(iter(years))
            if date_text == "00/00/0000":
                if not is_control or current is None:
                    fail(MESSAGE)
            else:
                current = day(date_text)
            if "SALDOANTERIOR" in compact:
                if opening is not None or rows:
                    fail(MESSAGE)
                opening = running = amount
                continue
            if is_control:
                if running is None or not rows:
                    fail(MESSAGE)
                if running != amount:
                    financial_mismatch()
                rows[-1] = replace(rows[-1], running_balance=float(amount),
                    source_line_refs=(*rows[-1].source_line_refs, (page.page_number, value.line_index)))
                checks += 1
                if compact in {"SALDO", "SALDOATUAL"}:
                    closing = amount
                    break
                continue
            if running is None or not label or rows and current < rows[-1].transaction.date:
                fail(MESSAGE)
            references = [line.text.strip() for line in cells
                          if columns["DOCUMENTO"] - .02 <= line.bbox["left"] < columns["HISTORICO"] - .02
                          and re.fullmatch(r"[\d.]+", line.text.strip())]
            if len(references) > 1:
                raise OcrFinancialAmbiguityError(MESSAGE)
            running += amount
            rows.append(_source(_row(page.page_number, anchor.line_index, current, label, amount,
                                     reference=references[0] if references else None), page, cells))
            if date_text != anchor.text.strip():
                rows[-1] = replace(rows[-1], warnings=["manual_review_recommended", "ocr_date_year_completed"])
        if closing is None and any(line.id not in used_dates for line in dates):
            raise OcrFinancialAmbiguityError(MESSAGE)
    if not rows:
        fail(MESSAGE)
    if closing is None:
        financial_mismatch()
        closing = Decimal(str(rows[-1].running_balance)) if rows[-1].running_balance is not None else running
    return VerifiedStatement(rows, LAYOUT, opening, closing, printed_balance_checks=checks)
