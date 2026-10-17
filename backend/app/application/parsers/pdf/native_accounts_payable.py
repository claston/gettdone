"""Read payable columns from native PDF text, including rotated page coordinates."""
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.models import NormalizedTransaction
from app.application.normalization.canonical import from_normalized_transaction
from app.application.normalization.canonical_metrics import build_canonical_quality_metrics
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf.models import PdfParseResult
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK
from app.application.pdf_layout_inference import PdfLayoutInference

LAYOUT = "accounts_payable_native_columns_v1"
PARSER = "accounts_payable_native_columns"
VALIDATION_MESSAGE = (
    "Não foi possível conferir todas as linhas do relatório de contas a pagar. "
    "Envie o PDF original completo com as colunas de datas, valores e situação legíveis."
)
DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
MONEY = re.compile(r"(?<![\d.,])-?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}(?![\d.,])")
HEADERS = ("Vencimento", "Pagamento", "Descrição", "Fornecedor", "Categoria", "Valor total", "Em aberto", "Situação")


@dataclass(frozen=True)
class Box:
    left: float
    bottom: float
    right: float
    top: float
    text: str
    index: int


def _rotate(rect, rotation, width, height):
    left, bottom, right, top = rect
    if rotation == 90:
        return bottom, height - right, top, height - left
    if rotation == 180:
        return width - right, height - top, width - left, height - bottom
    if rotation == 270:
        return width - top, left, width - bottom, right
    return rect


class PageGrid:
    def __init__(self, page, textpage):
        self.textpage = textpage
        self.rotation = page.get_rotation()
        self.width, self.height = page.get_size()

    def boxes(self, text):
        boxes = []
        search = self.textpage.search(text, match_case=False)
        try:
            while (match := search.get_next()) is not None:
                index, count = match
                chars = [self.textpage.get_charbox(i) for i in range(index, index + count)]
                rect = min(r[0] for r in chars), min(r[1] for r in chars), max(r[2] for r in chars), max(r[3] for r in chars)
                boxes.append(Box(*_rotate(rect, self.rotation, self.width, self.height), text, index))
        finally:
            search.close()
        return boxes

    def text(self, left, bottom, right, top):
        rect = (left, bottom, right, top)
        if self.rotation == 90:
            rect = (self.height - top, left, self.height - bottom, right)
        elif self.rotation == 180:
            rect = (self.width - right, self.height - top, self.width - left, self.height - bottom)
        elif self.rotation == 270:
            rect = (bottom, self.width - right, top, self.width - left)
        return " ".join(self.textpage.get_text_bounded(*rect).split())


def _money(text):
    values = MONEY.findall(text)
    if len(values) != 1:
        raise PdfFinancialValidationError(VALIDATION_MESSAGE)
    return Decimal(values[0].replace(".", "").replace(",", "."))


def _date(text):
    if DATE.fullmatch(text) is None:
        raise PdfFinancialValidationError(VALIDATION_MESSAGE)
    try:
        return datetime.strptime(text, "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise PdfFinancialValidationError(VALIDATION_MESSAGE) from exc


def parse_native_accounts_payable(raw_bytes: bytes) -> PdfParseResult:
    import pypdfium2 as pdfium

    canonical = []
    transactions = []
    source_pages = []
    total = Decimal(0)
    printed_total = None
    printed_count = None
    printed_paid = None
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw_bytes) as document:
        for page_number in range(1, len(document) + 1):
            page = document[page_number - 1]
            textpage = page.get_textpage()
            try:
                native_text = textpage.get_text_range()
                source_pages.append(native_text)
                grid = PageGrid(page, textpage)
                for label in grid.boxes("Total do Período"):
                    value = grid.text(label.right + 1, label.bottom - 2, grid.width, label.top + 2)
                    amount = _money(value)
                    if printed_total is not None and amount != printed_total:
                        raise PdfFinancialValidationError(VALIDATION_MESSAGE)
                    printed_total = amount
                for label in grid.boxes("Pagos (R$)"):
                    printed_paid = _money(grid.text(label.right + 1, label.bottom - 2, grid.width, label.top + 2))
                columns = []
                for due in grid.boxes(HEADERS[0]):
                    candidate = [next((box for box in grid.boxes(label)
                                       if abs(box.bottom - due.bottom) < max(4, (due.top - due.bottom) * 0.5)), None)
                                 for label in HEADERS]
                    if all(candidate) and all(a.left < b.left for a, b in zip(candidate, candidate[1:], strict=False)):
                        columns = candidate
                        break
                if not columns:
                    # Blank/filter-only pages have no table records to convert.
                    if DATE.search(native_text) and MONEY.search(native_text):
                        raise PdfFinancialValidationError(VALIDATION_MESSAGE)
                    continue
                footer_boundary = max((box.top for label in ("Totais do período", "Usuário:") for box in grid.boxes(label)
                                       if box.left < columns[1].left and box.top < columns[0].bottom), default=0)
                for label in grid.boxes("Total de registros"):
                    label_height = label.top - label.bottom
                    value = grid.text(label.left - 2, label.bottom - 3.3 * label_height,
                                      label.right + label_height, label.bottom - 0.2 * label_height)
                    if not value.isdigit():
                        raise PdfFinancialValidationError(VALIDATION_MESSAGE)
                    printed_count = int(value)
                anchors = sorted([
                    box for value in set(DATE.findall(native_text)) for box in grid.boxes(value)
                    if columns[0].left - 3 <= box.left < columns[1].left - 3
                    and footer_boundary < box.bottom and box.top < columns[0].bottom
                ], key=lambda box: -box.top)
                for index, anchor in enumerate(anchors):
                    height = max(3, anchor.top - anchor.bottom)
                    bottom = anchors[index + 1].top + height * 0.2 if index + 1 < len(anchors) else max(
                        anchor.bottom - 4 * height, footer_boundary + 2,
                    )
                    top = anchor.top + height * 0.5
                    cells = [grid.text(column.left - 2, bottom,
                                       columns[col_index + 1].left - 2 if col_index < 7 else grid.width, top)
                             for col_index, column in enumerate(columns)]
                    due_date = _date(cells[0])
                    amount, outstanding = _money(cells[5]), _money(cells[6])
                    status = normalize_upper_text(cells[7])
                    if amount < 0 or outstanding < 0 or outstanding > amount or not cells[2]:
                        raise PdfFinancialValidationError(VALIDATION_MESSAGE)
                    if status == "PAGO" and outstanding == 0:
                        date = _date(cells[1])
                        label, warnings = "PAGO", []
                    elif status in {"VENCIDO", "EM ABERTO", "A VENCER", "PENDENTE"} and outstanding == amount:
                        date, label, warnings = due_date, "PENDENTE", ["accounts_payable_pending"]
                    elif status in {"PAGO PARCIALMENTE", "PARCIAL", "PARCIALMENTE PAGO"} and 0 < outstanding < amount:
                        date, label, warnings = due_date, "PARCIAL/PENDENTE", ["accounts_payable_partial"]
                    else:
                        raise PdfFinancialValidationError(VALIDATION_MESSAGE)
                    description = f"{label} | {cells[2]} | Fornecedor: {cells[3]} | Categoria: {cells[4]}"
                    if warnings:
                        description += " | Em aberto: " + f"{outstanding:.2f}".replace(".", ",")
                    transaction = NormalizedTransaction(date, description, -float(amount), "outflow")
                    source_line = 1 + (textpage.get_text_range(0, anchor.index).count("\n") if anchor.index else 0)
                    transactions.append(transaction)
                    canonical.append(from_normalized_transaction(
                        transaction, layout_name=LAYOUT, source_page=page_number, source_line=source_line,
                        source_parser=PARSER, warnings=warnings, confidence=0.99,
                    ))
                    total += amount
            finally:
                textpage.close()
                page.close()
    filtered_paid_scope = (
        printed_total is not None and total != printed_total
        and printed_count == len(transactions) and printed_paid == total
        and all(not row.warnings for row in canonical)
        and "FILTROS APLICADOS" in normalize_upper_text("\n".join(source_pages))
    )
    if (not transactions and printed_total != 0) or (printed_total is not None and total != printed_total and not filtered_paid_scope):
        raise PdfFinancialValidationError(VALIDATION_MESSAGE)
    if printed_count is not None and printed_count != len(transactions):
        raise PdfFinancialValidationError(VALIDATION_MESSAGE)
    joined = "\n".join(source_pages)
    return PdfParseResult(
        transactions=transactions, canonical_transactions=canonical, layout=PdfLayoutInference(LAYOUT, 0.99, False),
        extracted_text=joined, source_page_texts=tuple(source_pages), parse_metrics={
            **build_canonical_quality_metrics(canonical),
            "selected_parser": PARSER, "parser_selection_reason": "native_payable_columns",
            "page_count": len(source_pages), "extracted_char_count": len(joined),
            "flattened_line_count": sum(len(text.splitlines()) for text in source_pages),
            "balance_consistency_checked": 0, "balance_consistency_failed": 0,
            "extraction_provider": "native_pdfium_text", "native_text_detected": 1, "textract_attempted": 0,
            "confidence_band": "high", "export_recommendation": "review_recommended",
            "export_recommendation_reason": "accounts_payable_filtered_report" if filtered_paid_scope else "accounts_payable_report",
        },
    )
