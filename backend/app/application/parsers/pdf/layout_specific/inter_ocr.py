"""Inter OCR statement columns, including daily headers printed before movements."""
import re
from dataclasses import dataclass
from decimal import Decimal

from app.application.document_extraction_models import ExtractedLine
from app.application.errors import InvalidFileContentError, OcrFinancialAmbiguityError, PdfFinancialValidationError
from app.application.normalization.date import FULL_MONTH_PATTERN, parse_statement_date
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.source_regions import evidence_regions

_NUMBER = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
_MONEY = re.compile(rf"^[+\-−]?\s*R?\$\s*{_NUMBER}$")
_BALANCE_MONEY = re.compile(rf"^[+\-−]?\s*(?:R?\$\s*)?{_NUMBER}$")
_DAILY = re.compile(rf"^(\d{{1,2}} DE (?:{FULL_MONTH_PATTERN}) DE \d{{4}}) SALDO DO DIA:\s*([+\-−]?\s*(?:R?\$\s*)?{_NUMBER})$")
_PERIOD = re.compile(r"^PERIODO:\s*(\d{2}/\d{2}/\d{4}) A (\d{2}/\d{2}/\d{4})$")
_MESSAGE = "Não foi possível conferir todas as linhas e os saldos do extrato Banco Inter. Confira se o PDF está completo e legível."


def _money(text):
    return Decimal(re.sub(r"[R$\s]", "", text).replace("−", "-").replace(".", "").replace(",", "."))


def _date(text):
    try:
        return parse_statement_date(text, fallback_year=None)
    except InvalidFileContentError as exc:
        raise PdfFinancialValidationError(_MESSAGE) from exc


@dataclass(frozen=True)
class _Source:
    page: int
    number: int
    item: ExtractedLine


def parse_inter_ocr(extraction):
    first = extraction.pages[0]
    positions = {normalize_upper_text(line.text): line for line in first.lines if line.bbox
                 and normalize_upper_text(line.text) in {"VALOR", "SALDO POR TRANSACAO"}}
    if len(positions) != 2:
        raise OcrFinancialAmbiguityError("Falta o cabeçalho das colunas do extrato Banco Inter. Inclua a primeira página do documento.")
    amount_x = positions["VALOR"].bbox["left"]
    balance_x = positions["SALDO POR TRANSACAO"].bbox["left"]
    if not .05 < balance_x - amount_x < .3:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    amount_left, balance_left = amount_x - .16, (amount_x + balance_x) / 2
    periods = {(_date(match[1]), _date(match[2])) for line in first.lines
               if (match := _PERIOD.fullmatch(normalize_upper_text(line.text)))}
    if len(periods) != 1:
        raise PdfFinancialValidationError(_MESSAGE)
    start, end = next(iter(periods))
    rows = []
    current_date = None
    expected_daily = None
    previous_balance = None
    daily_start = 0

    def check_daily():
        if current_date is not None and (len(rows) <= daily_start or Decimal(str(rows[-1].running_balance)) != expected_daily):
            raise PdfFinancialValidationError(_MESSAGE)

    for ordinal, page in enumerate(extraction.pages):
        numbered = []
        number = 1
        for item in sorted(page.lines, key=lambda line: line.line_index):
            if item.text:
                numbered.append(_Source(page.page_number, number, item))
                number += len(item.text.splitlines())
        footer_y = min((line.item.bbox["top"] for line in numbered if line.item.bbox
                        and normalize_upper_text(line.item.text).startswith("FALE COM A GENTE")), default=1.0)
        lower = min(positions["VALOR"].bbox["top"], positions["SALDO POR TRANSACAO"].bbox["top"]) - .006 if ordinal == 0 else 0
        body = [line for line in numbered if line.item.bbox and lower <= line.item.bbox["top"] < footer_y]
        if any(line.item.bbox["left"] >= amount_left and "$" in line.item.text
               and not _MONEY.fullmatch(line.item.text.strip()) for line in body):
            raise OcrFinancialAmbiguityError(_MESSAGE)
        if any(_MONEY.fullmatch(line.item.text.strip()) and not line.item.bbox for line in numbered):
            raise OcrFinancialAmbiguityError(_MESSAGE)
        headers = [(line, _DAILY.fullmatch(normalize_upper_text(line.item.text))) for line in body
                   if "SALDO DO DIA:" in normalize_upper_text(line.item.text)]
        if any(match is None for _, match in headers):
            raise PdfFinancialValidationError(_MESSAGE)
        amounts = [line for line in body if amount_left <= line.item.bbox["left"] < balance_left
                   and _MONEY.fullmatch(line.item.text.strip())]
        balances = [line for line in body if line.item.bbox["left"] >= balance_left
                    and _BALANCE_MONEY.fullmatch(line.item.text.strip())]
        used_balances = set()
        events = [(line.item.bbox["top"], "date", line, match) for line, match in headers]
        events.extend((line.item.bbox["top"], "movement", line, None) for line in amounts)
        events.sort(key=lambda event: event[0])
        for index, (top, kind, source, match) in enumerate(events):
            if kind == "date":
                check_daily()
                new_date = _date(match[1])
                if not start <= new_date <= end or current_date is not None and new_date <= current_date:
                    raise PdfFinancialValidationError(_MESSAGE)
                current_date, expected_daily, daily_start = new_date, _money(match[2]), len(rows)
                continue
            if current_date is None:
                raise PdfFinancialValidationError(_MESSAGE)
            candidates = [line for line in balances if abs(line.item.bbox["top"] - top) <= .006]
            if len(candidates) != 1 or candidates[0].item.id in used_balances:
                raise OcrFinancialAmbiguityError(_MESSAGE)
            checkpoint = candidates[0]
            used_balances.add(checkpoint.item.id)
            next_top = events[index + 1][0] - .006 if index + 1 < len(events) else footer_y
            description = [line for line in body if line.item.bbox["left"] < amount_left
                           and top - .006 <= line.item.bbox["top"] < next_top
                           and not _DAILY.fullmatch(normalize_upper_text(line.item.text))]
            if not description or not any(abs(line.item.bbox["top"] - top) <= .006 for line in description):
                raise OcrFinancialAmbiguityError(_MESSAGE)
            if any(word.id in financial.item.word_ids and word.text_type == "HANDWRITING"
                   for financial in (source, checkpoint) for word in page.words):
                raise OcrFinancialAmbiguityError(_MESSAGE)
            amount, balance = _money(source.item.text), _money(checkpoint.item.text)
            if previous_balance is not None and previous_balance + amount != balance:
                raise PdfFinancialValidationError(_MESSAGE)
            witnesses = [*description, source, checkpoint]
            anchor = description[0]
            rows.append(build_parsed_transaction(date=current_date, description=" ".join(line.item.text.strip() for line in description),
                amount=float(amount), running_balance=float(balance), source_page=anchor.page, source_line=anchor.number,
                has_explicit_amount_sign=True, source_regions=evidence_regions(page.page_number,
                    [line.item for line in witnesses], words=page.words),
                source_line_refs=tuple((line.page, line.number) for line in witnesses)))
            previous_balance = balance
        if len(used_balances) != len(balances):
            raise OcrFinancialAmbiguityError(_MESSAGE)
    check_daily()
    if not rows:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    return rows
