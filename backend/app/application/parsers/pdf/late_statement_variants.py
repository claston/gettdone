"""Controlled native exports with explicit signs and complete printed controls."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.bb_cash_controls import is_pending_deposit
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, _lines, _row, day, fail, money

VIACREDI = "viacredi_av_extrato_especial_signed_v1"
BB_ZERO = "banco_do_brasil_zero_date_daily_controls_v1"
UNICRED_CARD = "unicred_fatura_cartao_tracked_native_v1"
_SIGNED = re.compile(rf"({NUMBER})\s*\(([+-])\)$")
_MONTHS = dict(zip(("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"), range(1, 13)))


def compact(text):
    return upper(text).replace(" ", "")


def identify_late_statement(text):
    normalized = upper(text)
    joined = compact(text)
    if "VIACREDI AV - EXTRATO ESPECIAL" in normalized and re.search(r"\bPAG\s+\d+\b", normalized):
        return VIACREDI
    if ("EXTRATODECONTACORRENTE" in joined and "DIALOTEDOCUMENTOHISTORICOVALOR" in joined
            and re.search(r"00/00/0000[^\n]*SALDO\s+DO\s+DIA", upper_lines(text))):
        return BB_ZERO
    if all(marker in joined for marker in ("UNICRED", "RESUMODAFATURA", "DESPESAS/DEBITOS", "SALDOTOTAL")):
        return UNICRED_CARD
    return None


def upper_lines(text):
    return "\n".join(upper(line) for line in text.splitlines())


def parse_late_statement(pages, kind):
    return {VIACREDI: _viacredi, BB_ZERO: _bb_zero, UNICRED_CARD: _unicred_card}[kind](pages, kind)


def _viacredi(pages, kind):
    rows, opening, running = [], None, None
    checks = 0
    terminal = False
    pattern = re.compile(rf"^(\d{{2}}/\d{{2}}/\d{{2}})\s+(.+?)\s+({NUMBER})\s+([CD])(?:\s+({NUMBER})(-?))?$")
    for page, line, raw in _lines(pages):
        normalized = upper(raw)
        seed = re.search(rf"SALDO ANTERIOR:\s*({NUMBER})(-?)", normalized)
        if seed:
            value = money(seed[1]) * (-1 if seed[2] else 1)
            if opening is None:
                opening = running = value
            elif value != running:
                financial_mismatch()
        if "AVISO: ESTE EXTRATO SERA TARIFADO" in normalized:
            terminal = True
            continue
        if not re.match(r"^\d{2}/\d{2}/\d{2}\b", raw):
            continue
        match = pattern.fullmatch(raw)
        if match is None or running is None or terminal:
            fail()
        date = day(match[1], "%d/%m/%y")
        if rows and date < rows[-1].transaction.date:
            fail()
        amount = money(match[3]) * (-1 if match[4] == "D" else 1)
        running += amount
        balance = money(match[5]) * (-1 if match[6] else 1) if match[5] else None
        if balance is not None:
            if running != balance:
                financial_mismatch()
            checks += 1
        reference = re.search(r"\s+([\d.]+)$", match[2])
        description = match[2][:reference.start()].strip() if reference else match[2]
        rows.append(_row(page, line, date, description, amount, balance, reference[1] if reference else None))
    if not rows or not checks or rows[-1].running_balance is None or not terminal:
        fail()
    return VerifiedStatement(rows, kind, opening, running, printed_balance_checks=checks)


def _bb_lines(pages):
    """Join a year glyph emitted on the next physical page, never infer it."""
    lines = [[page, line, raw, ()] for page, line, raw in _lines(pages)]
    for index, item in enumerate(lines):
        page, line, raw, _ = item
        fragment = re.match(r"^(\d{2}/\d{2}/\d{3})(\s+.+)$", raw)
        if fragment is None:
            continue
        for candidate in lines[index+1:]:
            other_page, other_line, other_raw, _ = candidate
            if upper(other_raw).startswith(("EXTRATO", "CLIENTE", "AGENCIA", "LANCAMENTOS", "DIA ")):
                continue
            glyph = re.fullmatch(r"(\d)(?:\s+(.*))?", other_raw)
            if other_page != page+1 or glyph is None:
                fail("Uma data foi cortada na paginação do extrato Banco do Brasil. Envie o PDF completo.")
            item[2] = fragment[1] + glyph[1] + fragment[2]
            item[3] = ((other_page, other_line),)
            candidate[2] = glyph[2] or ""
            break
        else:
            fail()
    return lines


def _bb_zero(pages, kind):
    rows, opening, running, closing = [], None, None, None
    pending, pending_date, pending_refs = [], None, []
    checks = 0
    opening_date = None
    blocked = 0
    for page, line, raw, extra_refs in _bb_lines(pages):
        normalized = upper(raw)
        if closing is not None and normalized.startswith(("INFORMACOES ADICIONAIS", "LANCAMENTOS FUTUROS", "APLICACOES FINANCEIRAS")):
            break
        if is_pending_deposit(raw):
            if pending:
                fail()
            blocked += 1
            continue
        match = re.match(rf"^({DATE})\s+(.+)$", raw)
        if match:
            if pending:
                fail()
            date_text, rest = match.groups()
        else:
            date_text, rest = None, raw
        signed = _SIGNED.search(rest)
        label = compact(rest[:signed.start()] if signed else rest)
        if date_text == "00/00/0000":
            if not signed or "SALDODODIA" not in label or running is None or not rows:
                fail()
            value = money(signed[1]) * (-1 if signed[2] == "-" else 1)
            if value != running:
                financial_mismatch()
            rows[-1] = replace(rows[-1], running_balance=float(value), source_line_refs=(*rows[-1].source_line_refs, (page, line)))
            checks += 1
            continue
        if date_text and "SALDOANTERIOR" in label:
            if not signed or opening is not None or rows:
                fail()
            opening_date = day(date_text)
            opening = running = money(signed[1]) * (-1 if signed[2] == "-" else 1)
            continue
        if date_text and label == "SALDO":
            if not signed or not rows or closing is not None:
                fail()
            date = day(date_text)
            value = money(signed[1]) * (-1 if signed[2] == "-" else 1)
            if value != running or date < rows[-1].transaction.date:
                fail()
            closing = value
            rows[-1] = replace(rows[-1], running_balance=float(value), source_line_refs=(*rows[-1].source_line_refs, (page, line)))
            checks += 1
            continue
        if date_text:
            if opening is None or closing is not None:
                fail()
            pending_date = day(date_text)
            if pending_date <= opening_date or rows and pending_date < rows[-1].transaction.date:
                fail()
            pending = [rest[:signed.start()].strip() if signed else rest]
            pending_refs = [(page, line), *extra_refs]
        elif pending:
            if normalized.startswith(("EXTRATO", "CLIENTE", "AGENCIA", "DIA ", "LANCAMENTOS")):
                continue
            pending.append(rest[:signed.start()].strip() if signed else rest)
            pending_refs.append((page, line))
        else:
            if signed:
                fail()
            if rows and closing is None and not normalized.startswith(("EXTRATO", "CLIENTE", "AGENCIA", "DIA ", "LANCAMENTOS")):
                previous = rows[-1]
                rows[-1] = replace(previous, transaction=replace(previous.transaction,
                    description=previous.transaction.description + " " + raw),
                    source_line_refs=(*previous.source_line_refs, (page, line)))
            continue
        if signed:
            amount = money(signed[1]) * (-1 if signed[2] == "-" else 1)
            running += amount
            description = " ".join(pending)
            description = re.sub(r"^\d{5}\s+\d+\s+", "", description)
            row = _row(pending_refs[0][0], pending_refs[0][1], pending_date, description, amount)
            rows.append(replace(row, source_line_refs=tuple(pending_refs)))
            pending, pending_refs = [], []
    if pending or not rows or closing is None or not checks:
        fail()
    return VerifiedStatement(rows, kind, opening, closing, printed_balance_checks=checks, non_cash_entries=blocked)


def _invoice_currency(text):
    value = re.search(rf"(-?)R\$({NUMBER}|0)$", compact(text))
    if value is None:
        return None
    return (money(value[2]) if value[2] != "0" else Decimal(0)) * (-1 if value[1] else 1)


def _unicred_card(pages, kind):
    text = compact("\n".join(pages))
    due_matches = re.findall(r"VENCIMENTO:?(\d{2})(JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)(20\d{2})", text)
    if not due_matches or len(set(due_matches)) != 1:
        fail()
    due_day, due_month_name, due_year = due_matches[0]
    due_month, due_year = _MONTHS[due_month_name], int(due_year)
    day(f"{due_day}/{due_month:02}/{due_year}")
    summaries = {}
    in_summary = False
    for page_number, page_text in enumerate(pages, 1):
        for raw in page_text.splitlines():
            label = compact(raw)
            if label == "RESUMODAFATURA":
                in_summary = True
                continue
            if not in_summary:
                continue
            for key, marker in {"opening": "TOTALFATURAANTERIOR", "credits": "PAGAMENTOSRECEBIDOS",
                                "expenses": "DESPESAS/DEBITOS", "fees": "ENCARGOS", "closing": "SALDOTOTAL"}.items():
                if label.startswith(marker):
                    value = _invoice_currency(raw)
                    if value is None or key in summaries and summaries[key] != abs(value):
                        fail()
                    summaries[key] = abs(value)
    if set(summaries) != {"opening", "credits", "expenses", "fees", "closing"}:
        fail()
    if summaries["fees"] != 0 or summaries["opening"]-summaries["credits"]+summaries["expenses"] != summaries["closing"]:
        fail()
    rows = []
    in_table, subtotal, holder_total = False, Decimal(0), Decimal(0)
    subtotal_count = 0
    for page, line, raw in _lines(pages):
        normalized = compact(raw)
        if normalized.startswith("LANCAMENTOS") and "VISA" in normalized:
            in_table = True
            continue
        if normalized.startswith("FALECONOSCO") or normalized.startswith("RECIBODOPAGADOR"):
            in_table = False
        if not in_table:
            continue
        if normalized.startswith("TOTALDE"):
            value = _invoice_currency(raw)
            if value is None or value != subtotal:
                fail()
            holder_total += value
            subtotal = Decimal(0)
            subtotal_count += 1
            in_table = False
            continue
        dated = re.match(r"^(\d\s*\d)\s*/\s*([A-Za-z](?:\s*[A-Za-z]){2})\s+(.+)$", raw)
        if dated is None:
            continue
        value = _invoice_currency(dated[3])
        if value is None:
            fail()
        month_name = compact(dated[2])
        if month_name not in _MONTHS:
            fail()
        month = _MONTHS[month_name]
        date = day(f"{compact(dated[1])}/{month:02}/{due_year - (month > due_month)}")
        currency = re.search(r"-?\s*R\s*\$", dated[3])
        if currency is None:
            fail()
        description = dated[3][:currency.start()].strip()
        amount = -value  # Invoice charges are positive; payments/bonuses are negative.
        if "PAGAMENTORECEBIDO" not in compact(description):
            subtotal += value
        rows.append(_row(page, line, date, description, amount))
    incoming = sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount > 0), Decimal(0))
    outgoing = -sum((Decimal(str(r.transaction.amount)) for r in rows if r.transaction.amount < 0), Decimal(0))
    if (not rows or in_table or subtotal != 0 or not subtotal_count or incoming != summaries["credits"]
            or outgoing != summaries["expenses"] or holder_total != summaries["closing"]):
        fail()
    return VerifiedStatement(rows, kind, -summaries["opening"], -summaries["closing"],
                             printed_balance_checks=5+subtotal_count)


def read_unicred_invoice_pages(raw):
    """Preserve physical rows, including negative values emitted late in the stream."""
    from contextlib import closing
    from statistics import median

    import pypdfium2 as pdfium

    from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK

    pages = []
    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as document:
        for page_index in range(len(document)):
            with closing(document[page_index]) as page, closing(page.get_textpage()) as tp:
                chars = []
                for i in range(tp.count_chars()):
                    char = chr(pdfium.raw.FPDFText_GetUnicode(tp, i))
                    if not char.strip() or char == "\x00":
                        continue
                    left, bottom, right, top = tp.get_charbox(i, loose=True)
                    tight_left, tight_bottom, tight_right, tight_top = tp.get_charbox(i)
                    chars.append((tight_left, bottom, tight_right, top, char, tight_top-tight_bottom))
                if not chars:
                    fail()
                tolerance = median(c[3]-c[1] for c in chars) * .4
                groups = []
                for cell in sorted(chars, key=lambda c: (-(c[1]+c[3])/2, c[0])):
                    center = (cell[1]+cell[3])/2
                    group = next((g for g in groups if abs(g[0]-center) < tolerance), None)
                    if group is None:
                        groups.append([center, [cell]])
                    else:
                        group[1].append(cell)
                lines = []
                for _, cells in groups:
                    line, previous = "", None
                    ordered = sorted(cells)
                    spaces = [b[0]-a[2] for a, b in zip(ordered, ordered[1:]) if b[0] > a[2]]
                    gap = median(c[5] for c in cells)*.2 + (median(spaces) if spaces else 0)
                    for left, bottom, right, top, char, height in ordered:
                        if previous is not None and left-previous > gap:
                            line += " "
                        line += char
                        previous = right
                    lines.append(line)
                pages.append("\n".join(lines))
    return pages
