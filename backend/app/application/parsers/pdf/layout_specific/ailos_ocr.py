"""Ailos/Civia visual statement: explicit credit/debit columns and every balance."""

import re
from decimal import Decimal

from app.application.errors import InvalidFileContentError, OcrFinancialAmbiguityError, PdfFinancialValidationError
from app.application.normalization.date import parse_statement_date
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.source_regions import evidence_regions

AILOS_LAYOUT = "ailos_extrato_credito_debito_saldo_v1"
_MONEY = re.compile(r"^[+\-−]?\s*(?:R\$\s*)?(?:\d{1,3}(?:\.\d{3})+|\d+),\s*\d{2}$")
_DATE = re.compile(r"^(\S{1,2}/\d{2}/\d{4})(?:\s+(.*))?$")
_MESSAGE = "Não foi possível conferir todas as linhas, as colunas e os totais do extrato Ailos/Civia. Confira o documento original."


def is_ailos_statement(extraction):
    text = normalize_upper_text("\n".join(line.text for line in extraction.pages[0].lines)) if extraction.pages else ""
    return (
        bool(re.search(r"\b(?:AILOS|CIVIA)\b", text))
        and "COOPERATIVA" in text
        and bool(re.search(r"\bBANCO:?\s*085\b", text))
        and ("SALDO ANTERIOR" in text or all(word in text for word in ("DATA", "DESCRICAO", "DOCUMENTO", "SALDO")))
    )


def _money(text):
    return Decimal(re.sub(r"[R$\s]", "", text).replace("−", "-").replace(".", "").replace(",", "."))


def _date(text):
    try:
        return parse_statement_date(text, fallback_year=None)
    except InvalidFileContentError as exc:
        raise PdfFinancialValidationError(_MESSAGE) from exc


def _one(values):
    if len(values) != 1:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    return values[0]


def parse_ailos_ocr(extraction):
    first = extraction.pages[0]
    periods = {
        (_date(match[1]), _date(match[2]))
        for line in first.lines
        if (match := re.fullmatch(r"PERIODO:?\s*(\d{2}/\d{2}/\d{4}) A (\d{2}/\d{2}/\d{4})", normalize_upper_text(line.text)))
    }
    start, end = _one(list(periods))
    rows = []
    running = opening = None
    credits = debits = Decimal(0)
    totals = None
    for page in extraction.pages:
        numbered = []
        number = 1
        for item in sorted(page.lines, key=lambda line: line.line_index):
            if item.text:
                numbered.append((number, item))
                number += len(item.text.splitlines())
        headers = {
            normalize_upper_text(line.text).replace(" ", ""): line
            for _, line in numbered
            if line.bbox
            and normalize_upper_text(line.text).replace(" ", "")
            in {"DATA", "DESCRICAO", "DOCUMENTO", "CREDITO(R$)", "DEBITO(R$)", "SALDO(R$)"}
        }
        if len(headers) != 6:
            raise OcrFinancialAmbiguityError(_MESSAGE)
        desc_x, document_x = headers["DESCRICAO"].bbox["left"], headers["DOCUMENTO"].bbox["left"]
        credit_x, debit_x, balance_x = (headers[key].bbox["left"] for key in ("CREDITO(R$)", "DEBITO(R$)", "SALDO(R$)"))
        credit_left, debit_left, balance_left = (document_x + credit_x) / 2, (credit_x + debit_x) / 2, (debit_x + balance_x) / 2
        positioned = [(ref, line) for ref, line in numbered if line.bbox and line.bbox["top"] > headers["DATA"].bbox["top"] + 0.01]

        def cell_values(cells):
            result = {}
            witnesses = []
            for ref, item in cells:
                if item.bbox["left"] < credit_left:
                    continue
                if not _MONEY.fullmatch(item.text.strip()):
                    raise OcrFinancialAmbiguityError(_MESSAGE)
                if any(word.id in item.word_ids and word.text_type == "HANDWRITING" for word in page.words):
                    raise OcrFinancialAmbiguityError(_MESSAGE)
                kind = "credit" if item.bbox["left"] < debit_left else "debit" if item.bbox["left"] < balance_left else "balance"
                if kind in result:
                    raise OcrFinancialAmbiguityError(_MESSAGE)
                result[kind] = _money(item.text)
                witnesses.append((ref, item))
            return result, witnesses

        for _, label in positioned:
            text = normalize_upper_text(label.text)
            if text not in {"SALDO ANTERIOR", "TOTAL"}:
                continue
            values, _ = cell_values([(ref, line) for ref, line in positioned if abs(line.bbox["top"] - label.bbox["top"]) <= 0.006])
            if text == "SALDO ANTERIOR":
                if opening is not None or set(values) != {"balance"}:
                    raise PdfFinancialValidationError(_MESSAGE)
                opening = running = values["balance"]
            else:
                if totals is not None or set(values) != {"credit", "debit", "balance"}:
                    raise PdfFinancialValidationError(_MESSAGE)
                totals = values
        anchors = [
            (ref, line, _DATE.fullmatch(line.text.strip()))
            for ref, line in positioned
            if line.bbox["left"] < (headers["DATA"].bbox["left"] + desc_x) / 2 and _DATE.fullmatch(line.text.strip())
        ]
        terminal = min((line.bbox["top"] for _, line in positioned if normalize_upper_text(line.text) == "TOTAL"), default=1.0)
        if any(not line.bbox and _MONEY.fullmatch(line.text.strip()) for _, line in numbered):
            raise OcrFinancialAmbiguityError(_MESSAGE)
        if any(line.bbox["left"] >= credit_left and line.bbox["top"] <= terminal + .006
               and not _MONEY.fullmatch(line.text.strip()) for _, line in positioned):
            raise OcrFinancialAmbiguityError(_MESSAGE)
        used_values = set()
        for index, (ref, source, match) in enumerate(anchors):
            day = _date(match[1])
            if opening is None or not start <= day <= end or rows and day < rows[-1].transaction.date:
                raise PdfFinancialValidationError(_MESSAGE)
            top = source.bbox["top"]
            lower = (anchors[index - 1][1].bbox["top"] + top) / 2 if index else top - 0.006
            upper = (anchors[index + 1][1].bbox["top"] + top) / 2 if index + 1 < len(anchors) else terminal - 0.004
            cells = [(number, line) for number, line in positioned if lower <= line.bbox["top"] < upper]
            values, witnesses = cell_values(cells)
            if "balance" not in values or len(set(values) & {"credit", "debit"}) != 1:
                raise OcrFinancialAmbiguityError(_MESSAGE)
            if values.get("credit", Decimal(0)) < 0:
                raise PdfFinancialValidationError(_MESSAGE)
            amount = values["credit"] if "credit" in values else -abs(values["debit"])
            running += amount
            if running != values["balance"]:
                raise PdfFinancialValidationError(_MESSAGE)
            credits += max(amount, Decimal(0))
            debits += max(-amount, Decimal(0))
            description = [match[2]] if match[2] else []
            description.extend(
                line.text.strip()
                for _, line in cells
                if (headers["DATA"].bbox["left"] + desc_x) / 2 <= line.bbox["left"] < (desc_x + document_x) / 2
            )
            if not description:
                raise OcrFinancialAmbiguityError(_MESSAGE)
            document = [line.text.strip() for _, line in cells if (desc_x + document_x) / 2 <= line.bbox["left"] < credit_left]
            sources = [(ref, source), *[(number, line) for number, line in cells if line.bbox["left"] < credit_left], *witnesses]
            used_values.update(item.id for _, item in witnesses)
            rows.append(
                build_parsed_transaction(
                    date=day,
                    description=" ".join(description),
                    amount=float(amount),
                    running_balance=float(running),
                    external_reference_id=document[0] if len(document) == 1 else None,
                    source_page=page.page_number,
                    source_line=ref,
                    has_explicit_amount_sign=True,
                    source_regions=evidence_regions(page.page_number, [line for _, line in sources], words=page.words),
                    source_line_refs=tuple(dict.fromkeys((page.page_number, number) for number, _ in sources)),
                )
            )
        monetary = [line for _, line in positioned if line.bbox["left"] >= credit_left and _MONEY.fullmatch(line.text.strip())]
        allowed = {
            item.id
            for item in monetary
            if any(
                abs(item.bbox["top"] - line.bbox["top"]) <= 0.006
                for _, line in positioned
                if normalize_upper_text(line.text) in {"SALDO ANTERIOR", "TOTAL"}
            )
        }
        if any(line.id not in used_values | allowed for line in monetary):
            raise OcrFinancialAmbiguityError(_MESSAGE)
    if not rows or totals is None or (credits, -debits, running) != (totals["credit"], totals["debit"], totals["balance"]):
        raise PdfFinancialValidationError(_MESSAGE)
    return rows
