"""Verify Stone OCR cells against their printed columns and local ledger witnesses."""

import re
from dataclasses import dataclass, replace
from decimal import Decimal

from app.application.document_extraction_models import ExtractedLine, RawDocumentExtraction
from app.application.errors import OcrFinancialAmbiguityError
from app.application.normalization.pdf_amount_tokens import NUMBER_TOKEN, parse_pdf_amount
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.models import _ParsedTransaction
from app.application.source_regions import evidence_regions

_DATE = re.compile(r"^\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})$")
_MONEY = re.compile(
    rf"^(?P<sign>[+-]?)\s*(?:(?:R[$S]|\$)\s*)*(?P<inner_sign>[+-]?)\s*(?P<number>{NUMBER_TOKEN})\s*$",
    re.IGNORECASE,
)
_MESSAGE = (
    "Não foi possível confirmar um valor nas colunas do extrato Stone. "
    "Envie o PDF original ou uma imagem mais nítida para evitar confundir valor e saldo."
)


@dataclass
class _OcrRow:
    original_index: int
    source: tuple[int, int]
    direction: str | None
    description: str
    amount: Decimal | None
    balance: Decimal | None
    malformed_balance: str | None
    placeholder: bool
    items: list[ExtractedLine]
    refs: tuple[tuple[int, int], ...]
    direction_normalized: bool = False


def resolve_stone_ocr_rows(rows: list[_ParsedTransaction], extraction: RawDocumentExtraction) -> list[_ParsedTransaction]:
    resolved = list(rows)
    by_source = {(row.source_page, row.source_line): index for index, row in enumerate(rows)}
    records = []
    by_page = {page.page_number: page for page in extraction.pages}
    for page in extraction.pages:
        numbered = []
        number = 1
        for line in sorted(page.lines, key=lambda item: item.line_index):
            if line.text:
                numbered.append((number, line))
                number += len(line.text.splitlines())
        headers = _headers(numbered, page.words)
        if headers is None:
            _reject_unsafe_unpositioned_rows(rows, page.page_number)
            continue
        anchors = [
            (number, line)
            for number, line in numbered
            if line.bbox
            and re.match(r"^\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})(?:\s|$)", line.text.strip())
            and line.bbox["left"] < (headers["DATA"] + headers["TIPO"]) / 2
        ]
        page_rows = [row for row in rows if row.source_page == page.page_number]
        if len(anchors) != len(page_rows) or any((page.page_number, number) not in by_source for number, _ in anchors):
            # Merged OCR lines retain the established text parser; never shift positional provenance.
            _reject_unsafe_unpositioned_rows(rows, page.page_number)
            continue
        for index, (number, anchor) in enumerate(anchors):
            top = anchor.bbox["top"]
            lower = (anchors[index - 1][1].bbox["top"] + top) / 2 if index else top - 0.02
            upper = (anchors[index + 1][1].bbox["top"] + top) / 2 if index + 1 < len(anchors) else top + 0.016
            cells = [(ref, line) for ref, line in numbered if line.bbox and lower <= line.bbox["top"] < upper]
            records.append(_record(page, number, cells, headers, by_source[(page.page_number, number)]))
    for index, record in enumerate(records):
        row = resolved[record.original_index]
        warnings = list(row.warnings)
        if record.direction_normalized:
            warnings.append("ocr_direction_normalized")
        refs = record.refs
        items = list(record.items)
        amount, balance = record.amount, record.balance
        if amount is None or record.malformed_balance is not None:
            witnesses, inferred_amount, confirmed_balance = _recover(records, index, rows)
            if amount is None:
                amount = inferred_amount
                warnings.append("ocr_amount_recovered_from_balances")
            if record.malformed_balance is not None or balance != confirmed_balance:
                balance = confirmed_balance
                warnings.append("ocr_balance_recovered_from_context")
            refs = tuple(dict.fromkeys(ref for witness in witnesses for ref in witness.refs))
            items = [item for witness in witnesses for item in witness.items]
        if record.direction is None or balance is None:
            # Known amounts can remain readable without a balance, but their roles must agree.
            if amount is None or float(amount) != row.transaction.amount:
                raise OcrFinancialAmbiguityError(_MESSAGE)
            continue
        description = row.transaction.description
        if "ocr_amount_recovered_from_balances" in warnings:
            description = "Saída Tarifa"
        resolved[record.original_index] = replace(
            row,
            transaction=replace(
                row.transaction, amount=float(amount), type="inflow" if amount >= 0 else "outflow", description=description
            ),
            running_balance=float(balance),
            has_explicit_amount_sign=True,
            warnings=warnings,
            source_line_refs=refs,
            source_regions=[
                region
                for number in sorted({item.page_number for item in items})
                for region in evidence_regions(number, [item for item in items if item.page_number == number], words=by_page[number].words)
            ],
        )
    return resolved


def _headers(numbered, words=()):
    expected = ("DATA", "TIPO", "DESCRICAO", "VALOR", "SALDO", "CONTRAPARTE")
    found = {normalize_upper_text(line.text): line for _, line in numbered if line.bbox and normalize_upper_text(line.text) in expected}
    if set(found) != set(expected):
        # A single OCR LINE may merge DATA/TIPO/DESCRIÇÃO. WORD boxes still
        # carry their independent column coordinates and source identity.
        found = {normalize_upper_text(word.text): word for word in words if word.bbox and normalize_upper_text(word.text) in expected}
        if set(found) != set(expected):
            return None
    positions = [found[key].bbox["left"] for key in expected]
    if any(right - left < 0.02 for left, right in zip(positions, positions[1:])):
        return None
    if max(line.bbox["top"] for line in found.values()) - min(line.bbox["top"] for line in found.values()) > 0.02:
        return None
    return dict(zip(expected, positions, strict=True))


def _record(page, source_line, cells, headers, original_index):
    type_left = (headers["DATA"] + headers["TIPO"]) / 2
    desc_left = (headers["TIPO"] + headers["DESCRICAO"]) / 2
    amount_left = (headers["DESCRICAO"] + headers["VALOR"]) / 2
    balance_left = (headers["VALOR"] + headers["SALDO"]) / 2
    party_left = (headers["SALDO"] + headers["CONTRAPARTE"]) / 2
    type_cells = [line for _, line in cells if type_left <= line.bbox["left"] < desc_left]
    directions = {normalize_upper_text(line.text) for line in type_cells} & {"ENTRADA", "SAIDA"}
    original_directions = bool(directions)
    for line in type_cells:
        token = normalize_upper_text(line.text).replace(" ", "")
        directions.update(
            label
            for label in ("ENTRADA", "SAIDA")
            if len(token) == len(label) and sum(a != b for a, b in zip(token, label, strict=True)) == 1
        )
    directions.update(
        match[1]
        for _, line in cells
        if (match := re.match(r"^\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})\s+(ENTRADA|SAIDA)\b", normalize_upper_text(line.text)))
    )
    if len(directions) != 1:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    direction = directions.pop()
    description = " ".join(line.text for _, line in cells if desc_left <= line.bbox["left"] < amount_left)
    amounts = [line for _, line in cells if amount_left <= line.bbox["left"] < balance_left]
    balances = [line for _, line in cells if balance_left <= line.bbox["left"] < party_left]
    amounts = _financial_words_when_merged(page, cells, amounts, amount_left, balance_left)
    balances = _financial_words_when_merged(page, cells, balances, balance_left, party_left)
    amount, invalid_amount, placeholder = _value(amounts, page)
    balance, invalid_balance, _ = _value(balances, page)
    if invalid_amount:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    if amount is not None:
        amount = -abs(amount) if direction == "SAIDA" else abs(amount)
    relevant = [(ref, line) for ref, line in cells if line.bbox["left"] < party_left]
    return _OcrRow(
        original_index,
        (page.page_number, source_line),
        direction,
        normalize_upper_text(description),
        amount,
        balance,
        invalid_balance,
        placeholder or not amounts,
        [line for _, line in relevant],
        tuple((page.page_number, ref) for ref, _ in relevant),
        direction_normalized=not original_directions and bool(type_cells),
    )


def _financial_words_when_merged(page, cells, lines, left, right):
    if any(_MONEY.fullmatch(line.text.strip()) for line in lines):
        return lines
    owners = {word_id: line for _, line in cells for word_id in line.word_ids}
    words = [
        word for word in page.words if word.id in owners and word.bbox and left <= word.bbox["left"] < right and re.search(r"\d", word.text)
    ]
    if not words:
        return lines
    return [
        ExtractedLine(
            id=word.id,
            page_number=page.page_number,
            line_index=owners[word.id].line_index,
            text=word.text,
            bbox=word.bbox,
            confidence=word.confidence,
            word_ids=(word.id,),
        )
        for word in words
    ]


def _value(lines, page):
    if any(
        re.search(r"\d", line.text)
        and not _MONEY.fullmatch(line.text.strip())
        and (re.search(r"R\$|\$", line.text) or re.fullmatch(r"[\d.,?+\-\s]+", line.text))
        for line in lines
    ):
        raise OcrFinancialAmbiguityError(_MESSAGE)
    candidates = [line for line in lines if _MONEY.fullmatch(line.text.strip())]
    if len(candidates) > 1:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    placeholder = len(lines) == 1 and lines[0].text.strip() in {"R$", "$"}
    financial_cells = [line for line in lines if line in candidates or line.text.strip() in {"R$", "$"}]
    if any(word.id in line.word_ids and word.text_type == "HANDWRITING" for line in financial_cells for word in page.words):
        raise OcrFinancialAmbiguityError(_MESSAGE)
    if not candidates:
        return None, None, placeholder
    line = candidates[0]
    match = _MONEY.fullmatch(line.text.strip())
    number = match["number"]
    invalid = number if re.match(r"^0\.\d{3},", number) else None
    amount = Decimal(str(parse_pdf_amount(number)))
    if "-" in (match["sign"], match["inner_sign"]):
        amount = -amount
    return amount, invalid, placeholder


def _recover(records, index, rows):
    if index < 1 or index + 3 >= len(records):
        raise OcrFinancialAmbiguityError(_MESSAGE)
    previous, fee, credit, older, oldest = records[index - 1 : index + 4]
    witnesses = (previous, fee, credit, older, oldest)
    dates = [rows[record.original_index].transaction.date for record in witnesses]
    if len(set(dates[1:])) != 1 or dates[0] < dates[1]:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    if (
        fee.direction != "SAIDA"
        or fee.description != "TARIFA"
        or credit.direction != "ENTRADA"
        or not {"PIX", "MAQUININHA"}.issubset(set(re.findall(r"\w+", credit.description)))
    ):
        raise OcrFinancialAmbiguityError(_MESSAGE)
    if any(record.amount is None or record.balance is None or record.malformed_balance for record in (previous, credit, older, oldest)):
        raise OcrFinancialAmbiguityError(_MESSAGE)
    balance = credit.balance
    older_valid = older.balance - older.amount == oldest.balance
    if (
        not older_valid
        and older.direction == "SAIDA"
        and older.description == "TARIFA"
        and oldest.direction == "ENTRADA"
        and older.balance == oldest.balance
        and index + 4 < len(records)
    ):
        next_older = records[index + 4]
        older_valid = (
            next_older.balance is not None
            and next_older.malformed_balance is None
            and older.balance - older.amount - oldest.amount == next_older.balance
        )
        witnesses = (*witnesses, next_older)
    if previous.balance - previous.amount != balance or not older_valid or not Decimal("0") < balance - older.balance < credit.amount:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    inferred_amount = balance - older.balance - credit.amount
    if fee.amount is None:
        if not fee.placeholder or fee.balance != balance and not _single_digit_difference(fee.balance, balance):
            raise OcrFinancialAmbiguityError(_MESSAGE)
    elif fee.amount != inferred_amount:
        raise OcrFinancialAmbiguityError(_MESSAGE)
    if fee.malformed_balance:
        source_digits = fee.malformed_balance.replace(".", "").replace(",", "")
        confirmed_digits = format(balance, ".2f").replace(".", "")
        if len(confirmed_digits) != len(source_digits) + 1 or not confirmed_digits.endswith(source_digits):
            raise OcrFinancialAmbiguityError(_MESSAGE)
    return witnesses, inferred_amount, balance


def _single_digit_difference(source, confirmed):
    if source is None or confirmed is None:
        return False
    before, after = format(source, ".2f"), format(confirmed, ".2f")
    return len(before) == len(after) and sum(a != b for a, b in zip(before, after, strict=True)) == 1


def _reject_unsafe_unpositioned_rows(rows, page_number):
    if any(row.source_page == page_number and row.running_balance is None and "$" in row.transaction.description for row in rows):
        raise OcrFinancialAmbiguityError(_MESSAGE)
