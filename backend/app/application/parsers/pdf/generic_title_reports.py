"""One semantic column model for native and OCR payable/receivable reports."""
import re
import unicodedata
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from app.application.errors import PdfFinancialValidationError
from app.application.financial_event_projection import project_financial_document
from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.native_accounts_payable import PageGrid, _rotate
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK
from app.application.source_regions import SourceRegion
from app.application.title_report_detection import COLUMN_ALIASES, PAYABLES, column_role, identify_title_report, normalized_label

DATE = re.compile(r"(?<!\d)\d{2}/\d{2}/\d{4}(?!\d)")
MONEY = re.compile(r"(?<![\d.,])[+-]?(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}(?![\d.,])")
TOTAL = re.compile(r"\b(?:SUBTOTAL|TOTAL GERAL|TOTAL DO PER[IÍ]ODO|TOTAIS DO PER[IÍ]ODO|TOTAL|USU[AÁ]RIO)\b", re.I)
MESSAGE = ("Identificamos um relatório de contas a pagar ou a receber, mas não foi possível separar todas as colunas de datas e valores. "
           "Envie o PDF com os cabeçalhos e as linhas legíveis para converter em Excel ou CSV.")


@dataclass(frozen=True)
class Cell:
    text: str
    left: float
    top: float
    right: float
    bottom: float
    line: int
    role: str | None = None


def fail():
    raise PdfFinancialValidationError(MESSAGE)


def _columns(candidates):
    # Prefer the complete label over nested aliases, e.g. "Valor recebido" / "Valor".
    selected = []
    for cell in sorted(candidates, key=lambda c: -len(c.text)):
        if not any(min(cell.right, other.right) > max(cell.left, other.left)
                   and min(cell.bottom, other.bottom) > max(cell.top, other.top) for other in selected):
            selected.append(cell)
    groups = []
    for anchor in selected:
        group = [cell for cell in selected if abs(cell.top-anchor.top) < max(.012, anchor.bottom-anchor.top)]
        roles = [cell.role for cell in group]
        if "original" in roles and any(role in roles for role in ("due", "issue", "movement")) and any(
            role in roles for role in ("party", "reference", "description")
        ):
            if len(set(roles)) != len(roles):
                fail()
            groups.append(sorted(group, key=lambda c: c.left))
    return max(groups, key=len, default=None)


def _decimal(value):
    if not value.strip():
        return None
    clean = value.strip().replace("R$", "").strip()
    if not MONEY.fullmatch(clean):
        fail()
    return Decimal(clean.replace(".", "").replace(",", "."))


def _extra_columns(columns, candidates):
    top, bottom = min(c.top for c in columns), max(c.bottom for c in columns)
    extra = []
    for cell in candidates:
        if (top-.003 <= cell.top <= bottom+.003 and normalized_label(cell.text)
                and not any(min(cell.right, c.right) > max(cell.left, c.left) for c in columns)):
            extra.append(replace(cell, role=f"extra_{len(extra)}"))
    return sorted([*columns, *extra], key=lambda c: c.left)


def _date(value):
    if not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        fail()


def _status(raw):
    text = upper(raw)
    if "PARCIAL" in text:
        return "partial"
    if text in {"PAGO", "PAGA", "RECEBIDO", "RECEBIDA", "LIQUIDADO", "BAIXADO", "QUITADO"}:
        return "settled"
    if text in {"A PAGAR", "A RECEBER", "EM ABERTO", "PENDENTE", "VENCIDO", "A VENCER", "ABERTO"}:
        return "pending"
    return "cancelled" if text in {"CANCELADO", "CANCELADA"} else "unknown"


def _check(code, actual, expected, refs):
    return {"code": code, "status": "passed" if actual == expected else "failed", "actual": str(actual),
            "expected": str(expected), "source_refs": refs}


def _events_from_page(page_number, columns, dates, monetary, totals, read, document, header_top):
    date_column = next((cell for role in ("due", "issue", "movement") for cell in columns if cell.role == role), None)
    original = next(cell for cell in columns if cell.role == "original")
    bounds = {column.role: (column.left-.002, columns[i+1].left-.002 if i+1 < len(columns) else 1)
              for i, column in enumerate(columns)}
    def in_column(cell, role):
        left, right = bounds[role]
        return left <= cell.left < right
    total_rows = [cell for cell in totals if (cell.left < .6 or cell.top < header_top) and not (
        not upper(cell.text).startswith("SUBTOTAL")
        and any(in_column(date, date_column.role) and abs(date.top-cell.top) < .012 for date in dates)
    )]
    terminal = min((cell.top for cell in total_rows if cell.top > header_top+.003 and upper(cell.text).startswith(
        ("TOTAL GERAL", "TOTAIS DO PERIODO", "USUARIO"))), default=1)
    anchors = sorted([cell for cell in dates if in_column(cell, date_column.role) and cell.top > header_top+.003
                      and cell.top < terminal
                      and not any(abs(cell.top-total.top) < .012 for total in total_rows)], key=lambda c: c.top)
    used, regions = [], []
    for index, anchor in enumerate(anchors):
        footer = min((t.top for t in total_rows if t.top > anchor.top+.005), default=.98)
        bottom = min(anchors[index+1].top-.003 if index+1 < len(anchors) else footer-.003, footer-.003)
        top = max(header_top+.003, anchor.top-.004)
        values = {cell.role: read(*bounds[cell.role], top, bottom).strip() for cell in columns}
        amounts = {role: amount for role in ("original", "outstanding", "settled", "discount", "interest", "fine")
                   if (amount := _decimal(values.get(role, ""))) is not None}
        if "original" not in amounts:
            fail()
        dates_by_role = {role: value for role in ("due", "issue", "payment", "movement")
                         if (value := _date(values.get(role, ""))) is not None}
        if not dates_by_role or not any(values.get(role) for role in ("party", "description", "reference")):
            fail()
        refs = [{"page": page_number, "line": anchor.line}]
        raw_status = values.get("status", "")
        if not raw_status and re.search(r"SITUACAO\s*:\s*A (?:PAGAR|RECEBER)", upper(document["source_text"])):
            raw_status = "A pagar" if document["document_type"] == PAYABLES else "A receber"
        status = _status(raw_status)
        event = {"event_id": f"title_p{page_number}_l{anchor.line}",
            "event_type": "payable_title" if document["document_type"] == PAYABLES else "receivable_title",
            "status": status, "reference": values.get("reference") or None, "counterparty": values.get("party") or None,
            "dates": dates_by_role, "amounts": {key: format(value, ".2f") for key, value in amounts.items()},
            "amount": format(amounts["original"], ".2f"), "amount_basis": "original", "currency": "BRL", "cash_effect": None,
            "description": values.get("description") or values.get("party") or values["reference"], "source_refs": refs,
            "attributes": {"source_fields": {cell.text: values[cell.role] for cell in columns},
                           "raw_row": read(0, 1, top, bottom), "reported_status": raw_status,
                           **{role: values[role] for role in ("category", "tax_id") if values.get(role)}}}
        document["events"].append(event)
        document["validations"].append(_check("title_status_identified", status != "unknown", True, refs))
        if "outstanding" in amounts and status == "settled":
            document["validations"].append(_check("settled_title_outstanding", amounts["outstanding"], Decimal(0), refs))
        used.extend(cell for cell in monetary if in_column(cell, "original") and top <= cell.top < bottom)
        regions.append(SourceRegion(page_number, max(0, columns[0].left-.002), top,
                                    min(1, columns[-1].right+.02)-max(0, columns[0].left-.002), bottom-top))
    # A readable original amount without a dated title is never silently discarded.
    for cell in monetary:
        if in_column(cell, "original") and header_top+.003 < cell.top < terminal and cell not in used and not any(
            abs(cell.top-total.top) < .012 for total in total_rows
        ):
            fail()
    for total in total_rows:
        if upper(total.text).startswith("SUBTOTAL"):
            continue
        money = [cell for cell in monetary if abs(cell.top-total.top) < .012 and in_column(cell, original.role)]
        if not money and total.top < header_top:
            money = [cell for cell in monetary if abs(cell.top-total.top) < .012 and cell.left > total.right]
        if len(money) == 1:
            document["totals"].append((_decimal(money[0].text), {"page": page_number, "line": total.line}))
    return regions


def _document(kind, pages):
    if any(identify_title_report(page) not in {None, kind} for page in pages):
        raise PdfFinancialValidationError(
            "Este PDF mistura relatórios de contas a pagar e a receber. "
            "Envie cada tipo separadamente para preservar a natureza dos títulos.")
    notice = ("Relatório de contas a pagar." if kind == PAYABLES else "Relatório de contas a receber.") + (
        " Os títulos, valores e situações informados foram preservados para conferência. "
        "Eles não comprovam movimentação na conta bancária. Disponível em Excel e CSV; OFX não se aplica a este relatório.")
    return {"schema_version": "financial_events_v1", "document_type": kind, "origin": None,
        "evidence": ["generic_title_report_columns"], "metadata": {}, "events": [], "observations": [],
        "validations": [], "notice": notice, "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
        "requires_destination_account": False, "supports_row_edits": False, "reason": notice},
        "source_text": "\n".join(pages), "totals": []}


def _result(document, pages, regions, *, native):
    if not document["events"] and not (document["totals"] and document["totals"][-1][0] == 0):
        fail()
    # A final global total validates the report; intermediate totals may be page/subgroup totals.
    totals = document.pop("totals")
    if totals:
        total, ref = totals[-1]
        actual = sum((Decimal(e["amount"]) for e in document["events"]), Decimal(0))
        document["validations"].append(_check("titles_original_total", actual, total, [ref]))
    document.pop("source_text")
    result = project_financial_document(document, pages, native=native)
    return replace(result, parse_metrics={**result.parse_metrics, "balance_consistency_checked": 0, "balance_consistency_failed": 0},
                   canonical_transactions=[replace(row, source_regions=[region])
        for row, region in zip(result.canonical_transactions, regions, strict=True)])


def parse_native_title_report(raw, pages):
    kind = identify_title_report("\n".join(pages))
    if kind is None:
        return None
    import pypdfium2 as pdfium

    document, regions, previous = _document(kind, pages), [], None
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as pdf:
        for number in range(len(pdf)):
            with closing(pdf[number]) as page, closing(page.get_textpage()) as textpage:
                grid = PageGrid(page, textpage)
                text = "".join(chr(pdfium.raw.FPDFText_GetUnicode(textpage, i)) for i in range(textpage.count_chars()))
                folded_parts, original_indices = [], []
                for index, char in enumerate(text):
                    part = "".join(c for c in unicodedata.normalize("NFKD", char.upper()) if not unicodedata.combining(c))
                    folded_parts.append(part)
                    original_indices.extend([index]*len(part))
                folded = "".join(folded_parts)
                def scan(pattern, role=None):
                    cells = []
                    for match in pattern.finditer(folded):
                        start, end = original_indices[match.start()], original_indices[match.end()-1]+1
                        rects = [_rotate(textpage.get_charbox(i), grid.rotation, grid.width, grid.height)
                                 for i in range(start, end) if not text[i].isspace()]
                        if not rects:
                            continue
                        left, bottom = min(r[0] for r in rects), min(r[1] for r in rects)
                        right, top = max(r[2] for r in rects), max(r[3] for r in rects)
                        cells.append(Cell(text[start:end], left/grid.width, 1-top/grid.height,
                                          right/grid.width, 1-bottom/grid.height, text[:start].count("\n")+1, role))
                    return cells
                candidates = []
                for role, aliases in COLUMN_ALIASES.items():
                    for alias in aliases:
                        pattern = re.compile(r"(?<!\w)" + re.escape(upper(alias)).replace(r"\ ", r"\s+") + r"(?!\w)")
                        candidates.extend(scan(pattern, role))
                columns = _columns(candidates)
                if columns:
                    # Discover additional headings geometrically; an unfamiliar
                    # ERP field stays a column instead of joining a supplier.
                    top, bottom = min(c.top for c in columns), max(c.bottom for c in columns)
                    glyphs = []
                    for index, char in enumerate(text):
                        if char.isspace() or char == "\x00":
                            continue
                        left, low, right, high = _rotate(textpage.get_charbox(index), grid.rotation, grid.width, grid.height)
                        y = 1-high/grid.height
                        if top-.003 <= y <= bottom+.003:
                            glyphs.append(Cell(char, left/grid.width, y, right/grid.width, 1-low/grid.height, index+1))
                    runs = []
                    for glyph in sorted(glyphs, key=lambda c: c.left):
                        if not runs or glyph.left-runs[-1].right > .012 or abs(glyph.top-runs[-1].top) > .006:
                            runs.append(glyph)
                        else:
                            previous_glyph = runs[-1]
                            gap = " " if glyph.left-previous_glyph.right > .002 else ""
                            runs[-1] = replace(previous_glyph, text=previous_glyph.text+gap+glyph.text, right=glyph.right,
                                               top=min(previous_glyph.top, glyph.top), bottom=max(previous_glyph.bottom, glyph.bottom))
                    runs = [replace(run, text=grid.text(run.left*grid.width-.2, (1-run.bottom)*grid.height-.2,
                                                       run.right*grid.width+.2, (1-run.top)*grid.height+.2)) for run in runs]
                    columns = _extra_columns(columns, runs)
                if columns is None and not DATE.search(text) and not MONEY.search(text):
                    continue
                header_top = max(cell.bottom for cell in columns) if columns else 0
                columns = columns or previous
                if columns is None:
                    fail()
                previous = columns
                def read(left, right, top, bottom):
                    return grid.text(left*grid.width, (1-bottom)*grid.height, right*grid.width, (1-top)*grid.height)
                regions += _events_from_page(number+1, columns, scan(DATE), scan(MONEY), scan(TOTAL), read, document, header_top)
    return _result(document, pages, regions, native=True)


def parse_ocr_title_report(extraction):
    pages = ["\n".join(line.text for line in page.lines) for page in extraction.pages]
    kind = identify_title_report("\n".join(pages))
    if kind is None:
        return None
    document, regions, previous = _document(kind, pages), [], None
    for page in extraction.pages:
        def cell(line, role=None):
            box = line.bbox
            return Cell(line.text, box["left"], box["top"], box["left"]+box["width"], box["top"]+box["height"],
                        getattr(line, "line_index", getattr(line, "word_index", 1)), role)
        source = [cell(line) for line in page.lines if getattr(line, "bbox", None)]
        candidates = [replace(item, role=role) for item in source if (role := column_role(item.text))]
        columns = _columns(candidates)
        if columns:
            columns = _extra_columns(columns, source)
        header_top = max(c.bottom for c in columns) if columns else 0
        columns = columns or previous
        if columns is None:
            fail()
        previous = columns
        data = [cell(word) for word in page.words if word.bbox] if page.words else source
        def read(left, right, top, bottom):
            selected = sorted([c for c in data if left <= c.left < right and top <= c.top < bottom],
                              key=lambda c: (round(c.top, 2), c.left))
            return " ".join(c.text for c in selected)
        regions += _events_from_page(page.page_number, columns, [c for c in source if DATE.fullmatch(c.text)],
            [c for c in source if MONEY.fullmatch(c.text)], [c for c in source if TOTAL.match(upper(c.text))], read, document, header_top)
    return _result(document, pages, regions, native=False)
