"""Read Unicred's debit/credit grid from native text coordinates, without rendering/OCR."""
from __future__ import annotations

import re
from dataclasses import dataclass
from threading import Lock

from app.application.errors import InvalidFileContentError
from app.application.normalization.pdf_amount_tokens import parse_pdf_amount
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.layout_specific.shared import build_parsed_transaction
from app.application.parsers.pdf.models import _ParsedTransaction

UNICRED_NATIVE_COLUMNS_LAYOUT = "unicred_extrato_conta_corrente_debito_credito_v1"
_DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
_MONEY = re.compile(r"(?<![\d,.])[+\-\u2212]?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}(?![\d,.])")
_PDFIUM_LOCK = Lock()


@dataclass(frozen=True)
class _TextBox:
    text: str
    left: float
    bottom: float
    right: float
    top: float


@dataclass(frozen=True)
class NativeUnicredGrid:
    rows: list[_ParsedTransaction]
    balance_checkpoints: dict[int, float]
    nonfinancial_rows: int
    source_pages: tuple[str, ...]


def parse_native_unicred_grid(raw_bytes: bytes, page_texts: list[str]) -> NativeUnicredGrid | None:
    header_lines = []
    for line in page_texts[0].splitlines()[:12] if page_texts else []:
        if _DATE.match(line.lstrip()):
            break
        header_lines.append(line)
    header = normalize_upper_text(" ".join(header_lines))
    if not (
        "UNICRED" in header and "EXTRATO DE CONTA CORRENTE" in header
        and re.search(r"\bDATA\b.*\bDEBITO\s+CREDITO\s+SALDO(?:\b|HISTORICO\b)", header)
    ):
        return None

    # PDFium is already shipped in the API/worker. Only its native text APIs are used.
    import pypdfium2 as pdfium

    rows: list[_ParsedTransaction] = []
    checkpoints: dict[int, float] = {}
    skipped = 0
    source_pages = []
    source_iterator = iter(page_texts)
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw_bytes) as document:
        for page_index in range(len(document)):
            page = document[page_index]
            textpage = page.get_textpage()
            try:
                native_text = textpage.get_text_range()
                if not native_text.strip():
                    source_pages.append("")
                    continue
                source_text = next(source_iterator, None)
                if source_text is None:
                    raise InvalidFileContentError("Unicred native page text is incomplete.")
                source_pages.append(source_text)
                columns = _find_columns(textpage)
                if columns is None:
                    raise InvalidFileContentError("Unicred native grid is missing its column headings.")
                characters = []
                for char_index in range(textpage.count_chars()):
                    char = chr(pdfium.raw.FPDFText_GetUnicode(textpage, char_index))
                    if char not in {"\x00", "\r", "\n"}:
                        characters.append(_TextBox(char, *textpage.get_charbox(char_index, loose=True)))
                anchors = sorted(
                    (box for value in set(_DATE.findall(native_text)) for box in _find_boxes(textpage, value)
                     if box.right < columns["document"].left and box.top < columns["date"].bottom),
                    key=lambda box: -box.bottom,
                )
                money_boxes = [
                    box for value in {m[0] for m in _MONEY.finditer(native_text)}
                    for box in _find_boxes(textpage, value)
                ]
                opening = _opening_balance(textpage, money_boxes, columns)
                if page_index == 0 and opening is not None:
                    checkpoints[0] = opening
                source_lines = iter(enumerate(source_text.splitlines(), 1))
                for index, anchor in enumerate(anchors):
                    source_line = next((n for n, text in source_lines if text.lstrip().startswith(anchor.text)), None)
                    if source_line is None:
                        raise InvalidFileContentError("Unicred native dates cannot be traced to source text.")
                    row_money = _row_money(anchor, money_boxes, columns)
                    amounts = {role: parse_pdf_amount(box.text) for role, box in row_money.items()}
                    financial = [(role, value) for role, value in amounts.items() if role != "balance"]
                    movement = [(role, value) for role, value in financial if value != 0] or financial
                    if not movement:
                        history = normalize_upper_text(textpage.get_text_bounded(
                            columns["description"].left - 2, anchor.bottom - 3, columns["debit"].left - 3, anchor.top + 3,
                        ))
                        if history.startswith(("ALTERACAO TAXA", "ALTERACAO DE TAXA", "ALTERACAO DE LIMITE")):
                            skipped += 1
                            continue
                        raise InvalidFileContentError("Unicred native dated row is missing monetary cells.")
                    if len(movement) != 1:
                        raise InvalidFileContentError("Unicred native row has both debit and credit amounts.")
                    role, amount = movement[0]
                    if role == "credit" and amount < 0:
                        raise InvalidFileContentError("Unicred native row has a negative value in the credit column.")
                    amount = -abs(amount) if role == "debit" else amount
                    bottom = anchors[index + 1].top + 2 if index + 1 < len(anchors) else anchor.bottom - max(
                        22, 3 * (anchor.top - anchor.bottom),
                    )
                    if index + 1 == len(anchors):
                        footers = [box for label in ("A Transporte", "A Transportar", "Saldo Atual")
                                   for box in _find_boxes(textpage, label)
                                   if box.left < columns["description"].left - 2 and box.top < anchor.bottom]
                        if footers:
                            bottom = max(box.top for box in footers) + 2
                    top = anchor.top + 3
                    history_left = columns["description"].left - 2
                    history_right = min(box.left for box in row_money.values()) - 1
                    description = _read_history(characters, history_left, bottom, history_right, top)
                    description = " ".join(description.split())
                    reference = " ".join(textpage.get_text_bounded(
                        anchor.right + 2, anchor.bottom - 3, history_left - 1, top,
                    ).split())
                    if not description:
                        raise InvalidFileContentError("Unicred native financial row is missing its history.")
                    rows.append(build_parsed_transaction(
                        date=parse_row_date(anchor.text, fallback_year=None), description=description, amount=amount,
                        source_page=page_index + 1, source_line=source_line, running_balance=amounts.get("balance"),
                        external_reference_id=reference or None, has_explicit_amount_sign=True,
                    ))
            finally:
                textpage.close()
                page.close()
    if not rows:
        raise InvalidFileContentError("Unicred native grid has no financial movements.")
    return NativeUnicredGrid(rows=rows, balance_checkpoints=checkpoints, nonfinancial_rows=skipped, source_pages=tuple(source_pages))


def _find_boxes(textpage, text: str) -> list[_TextBox]:
    search = textpage.search(text, match_case=False)
    boxes: list[_TextBox] = []
    try:
        while (match := search.get_next()) is not None:
            index, count = match
            rectangles = [textpage.get_charbox(i) for i in range(index, index + count)]
            boxes.append(_TextBox(text, min(r[0] for r in rectangles), min(r[1] for r in rectangles),
                                  max(r[2] for r in rectangles), max(r[3] for r in rectangles)))
    finally:
        search.close()
    return boxes


def _find_columns(textpage) -> dict[str, _TextBox] | None:
    candidates = {
        role: [box for label in labels for box in _find_boxes(textpage, label)]
        for role, labels in {
            "date": ("Data",), "document": ("Nr.Docum.", "N.Doc."), "description": ("Histórico", "Historico"),
            "debit": ("Débito", "Debito"), "credit": ("Crédito", "Credito"), "balance": ("Saldo",),
        }.items()
    }
    for debit in candidates["debit"]:
        columns = {role: next((box for box in boxes if abs(box.bottom - debit.bottom) < 4), None)
                   for role, boxes in candidates.items()}
        if all(columns.values()) and (
            columns["date"].left < columns["document"].left < columns["description"].left
            < columns["debit"].left < columns["credit"].left < columns["balance"].left
        ):
            return columns
    return None


def _row_money(anchor: _TextBox, boxes: list[_TextBox], columns: dict[str, _TextBox]) -> dict[str, _TextBox]:
    amounts: dict[str, _TextBox] = {}
    for box in boxes:
        tolerance = max(2, 0.75 * (columns["date"].top - columns["date"].bottom))
        if abs(box.bottom - anchor.bottom) > tolerance:
            continue
        role = min(("debit", "credit", "balance"), key=lambda r: abs(box.right - columns[r].right))
        horizontal_tolerance = max(2, 0.75 * (columns[role].top - columns[role].bottom))
        if abs(box.right - columns[role].right) > horizontal_tolerance:
            continue
        previous = amounts.get(role)
        # A search for 10,00 can also match the end of 110,00. Retain the
        # full amount (including a printed minus sign) at each column edge.
        if previous is None or box.left < previous.left:
            amounts[role] = box
    return amounts


def _opening_balance(textpage, boxes: list[_TextBox], columns: dict[str, _TextBox]) -> float | None:
    for label in _find_boxes(textpage, "Saldo Anterior"):
        amounts = _row_money(label, boxes, columns)
        if "balance" in amounts:
            return parse_pdf_amount(amounts["balance"].text)
    return None


def _read_history(characters: list[_TextBox], left: float, bottom: float, right: float, top: float) -> str:
    selected = [char for char in characters if left <= (char.left + char.right) / 2 < right
                and bottom <= (char.bottom + char.top) / 2 <= top]
    lines: list[list[_TextBox]] = []
    for char in sorted(selected, key=lambda c: (-c.bottom, c.left)):
        if not lines or abs(lines[-1][0].bottom - char.bottom) > 2:
            lines.append([])
        lines[-1].append(char)
    return "\n".join("".join(char.text for char in sorted(line, key=lambda c: c.left)).strip() for line in lines)
