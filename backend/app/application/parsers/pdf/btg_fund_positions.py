"""BTG fund positions and acquisition lots are observations, never bank entries."""
import re
from contextlib import closing
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK
from app.application.parsers.pdf.verified_statements import NUMBER, day, fail, money

D = r"\d{2}/\d{2}/\d{2}"
QUANTITY = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{4,12}"


def read_btg_native_pages(raw):
    import pypdfium2 as pdfium

    # PDFium preserves BTG's spaced bold totals and maps its embedded glyphs.
    # This reads native text only; it does not rasterize or invoke OCR.
    with _PDFIUM_LOCK, closing(pdfium.PdfDocument(raw)) as document:
        pages = []
        for page in document:
            with closing(page), closing(page.get_textpage()) as textpage:
                pages.append(textpage.get_text_range())
        return pages


def _decimal(token):
    if token == "-":
        return Decimal(0)
    if not re.fullmatch(rf"(?:{NUMBER}|{QUANTITY})", token):
        fail("Um campo de posição ou quantidade BTG não está legível.")
    return money(token)


def _packet(lines, prefix):
    packets, current, ref = [], [], None
    for text, source in lines:
        if re.match(prefix, text):
            if current:
                packets.append((" ".join(current), ref))
            current, ref = [text], source
        elif current:
            if re.fullmatch(rf"(?:{NUMBER}|{QUANTITY}|-)(?:\s+(?:{NUMBER}|{QUANTITY}|-))*", text):
                current.append(text)
            else:
                packets.append((" ".join(current), ref))
                current, ref = [], None
    if current:
        packets.append((" ".join(current), ref))
    return packets


def parse_btg_fund_positions(pages):
    lines_by_page = [[(text.strip(), {"page": page, "line": number})
                      for number, text in enumerate(content.splitlines(), 1) if text.strip()]
                     for page, content in enumerate(pages, 1)]
    text = upper(" ".join(pages))
    period = re.search(rf"PERIODO DE\s+({D})\s+(?:A|ATE)\s+({D})", text)
    if not period:
        fail()
    start, end = (day(period[i], "%d/%m/%y") for i in (1, 2))
    if start > end:
        fail()
    position_pages = [lines for lines in lines_by_page if "POSICAO PORTFOLIO DE FUNDOS" in upper(" ".join(t for t, _ in lines))]
    lot_pages = [lines for lines in lines_by_page if re.search(r"DETALHAMENTO\s*[›>]", " ".join(t for t, _ in lines), re.I)]
    cash_pages = [lines for lines in lines_by_page if re.match(r"CONTA CORRENTE\b", upper(lines[0][0]))]
    if len(position_pages) != 1 or len(lot_pages) != 1 or len(cash_pages) != 1:
        fail("Esta variante BTG exige as posições, os lotes e a posição de conta corrente completos de um único fundo.")
    observations, validations = [], []

    def check(code, actual, expected, refs, tolerance=Decimal(0)):
        if abs(actual - expected) > tolerance:
            fail("Os campos do relatório BTG não fecham com as posições e os totais impressos.")
        validations.append({"code": code, "status": "passed", "actual": str(actual),
                            "expected": str(expected), "source_refs": refs,
                            **({"tolerance": str(tolerance)} if tolerance else {})})

    def observe(kind, field, value, label, dates, ref, attributes=None):
        observations.append({"observation_type": kind, "dates": dates, "amounts": {field: format(value, ".2f")},
                             "attributes": {"label": label, **(attributes or {})}, "source_refs": [ref]})

    summaries = [(t, ref) for lines in lines_by_page for t, ref in lines
                 if re.fullmatch(rf"Fundos de Investimento(?:\s+{NUMBER}){{4}}", t, re.I)]
    if len(summaries) != 1:
        fail()
    summary = [money(v) for v in re.findall(NUMBER, summaries[0][0])]
    summary_lines = lines_by_page[summaries[0][1]["page"] - 1]
    printed_dates = [day(t, "%d/%m/%y") for t, _ in summary_lines if re.fullmatch(D, t)]
    if len(printed_dates) != 4 or printed_dates[0] != printed_dates[1] or printed_dates[0] >= start or printed_dates[2:] != [end, end]:
        fail()
    for field, label, value, date in zip(
        ("opening_gross", "opening_net", "closing_gross", "closing_net"),
        ("Saldo bruto anterior", "Saldo líquido anterior", "Saldo bruto final", "Saldo líquido final"),
        summary, (printed_dates[0], printed_dates[0], end, end), strict=True,
    ):
        observe("fund_position", field, value, label, {"period_start": start, "period_end": end, "as_of": date}, summaries[0][1])
    summary_totals = [(t, ref) for lines in lines_by_page for t, ref in lines
                      if re.fullmatch(rf"Total(?:\s+{NUMBER}){{4}}", t, re.I)]
    if len(summary_totals) != 1:
        fail()
    for actual, expected in zip([money(v) for v in re.findall(NUMBER, summary_totals[0][0])], summary, strict=True):
        check("btg_summary_total", actual, expected, [summary_totals[0][1]])
    positions = _packet(position_pages[0], rf"^{D}\s+{NUMBER}\s+{QUANTITY}")
    if len(positions) != 1:
        fail()
    raw_position, position_ref = positions[0]
    date, *tokens = raw_position.split()
    if len(tokens) != 8 or day(date, "%d/%m/%y") != end:
        fail()
    opening_net, quantity, quote, gross, irrf, iof, net, income = map(_decimal, tokens)
    for code, actual, expected in (("btg_opening_net", opening_net, summary[1]),
                                    ("btg_gross_position", gross, summary[2]),
                                    ("btg_net_position", net, summary[3]),
                                    ("btg_taxes", gross-irrf-iof, net)):
        check(code, actual, expected, [position_ref])
    for field, value, label in (("gross", gross, "Saldo bruto"), ("irrf", irrf, "Provisão de IR"),
                                ("iof", iof, "Provisão de IOF"), ("net", net, "Saldo líquido"),
                                ("income", income, "Variação nominal")):
        observe("fund_position", field, value, label, {"as_of": end}, position_ref,
                {"quantity": str(quantity), "unit_quote": str(quote)})
    position_totals = _packet(position_pages[0], r"^Total em fundos\s+")
    if len(position_totals) != 1:
        fail()
    actual = list(map(_decimal, position_totals[0][0].split()[3:]))
    if len(actual) != 5:
        fail()
    for a, expected in zip(actual, (gross, irrf, iof, net, income), strict=True):
        check("btg_funds_total", a, expected, [position_totals[0][1]])
    lots = _packet(lot_pages[0], rf"^{D}\s+")
    if not lots:
        fail()
    totals = [Decimal(0)] * 5  # quantity, gross, IR, IOF, net
    quantity_rounding = Decimal(0)
    historical = True
    for raw_lot, ref in lots:
        date, *values = raw_lot.split()
        if len(values) != 7:
            fail()
        purchased = day(date, "%d/%m/%y")
        if purchased > end:
            fail()
        historical &= purchased < start
        qty, unit, principal, lot_gross, lot_ir, lot_iof, lot_net = map(_decimal, values)
        quantity_rounding += Decimal(1).scaleb(qty.as_tuple().exponent) / 2
        check("btg_lot_net", lot_gross-lot_ir-lot_iof, lot_net, [ref])
        for i, value in enumerate((qty, lot_gross, lot_ir, lot_iof, lot_net)):
            totals[i] += value
        for field, value, label in (("principal", principal, "Compra ajustada"), ("gross", lot_gross, "Saldo bruto do lote"),
                                    ("irrf", lot_ir, "Provisão de IR do lote"), ("iof", lot_iof, "Provisão de IOF do lote"),
                                    ("net", lot_net, "Saldo líquido do lote")):
            observe("investment_lot", field, value, label, {"application": purchased, "as_of": end}, ref,
                    {"quantity": str(qty), "unit_quote": str(unit)})
    detail_total = _packet(lot_pages[0], r"^Total\s+")
    if len(detail_total) != 1:
        fail()
    printed = list(map(_decimal, detail_total[0][0].split()[1:]))
    if len(printed) != 5:
        fail()
    # Each lot and each aggregate has its own displayed precision. Bound the
    # accumulated quantity rounding by half a unit at each printed precision.
    # Monetary checks retain a zero tolerance.
    for index, (a, expected, aggregate) in enumerate(zip(totals, (quantity, gross, irrf, iof, net), printed, strict=True)):
        tolerance = quantity_rounding + Decimal(1).scaleb(expected.as_tuple().exponent)/2 if index == 0 else Decimal(0)
        check("btg_lot_aggregate", a, expected, [ref for _, ref in lots], tolerance)
        tolerance = quantity_rounding + Decimal(1).scaleb(aggregate.as_tuple().exponent)/2 if index == 0 else Decimal(0)
        check("btg_lot_printed_total", a, aggregate, [detail_total[0][1]], tolerance)
    if historical:
        check("btg_position_change", net-opening_net, income, [position_ref])
    cash = [(t, ref) for t, ref in cash_pages[0] if re.match(rf"^{D}\b", t)]
    if len(cash) != 1 or cash[0][0].split() != [period[2], "-"]:
        fail("O relatório BTG contém conta corrente ou movimentos não cobertos por esta variante de posições. "
             "Envie o extrato de movimentação separado.")
    observe("cash_position", "net", Decimal(0), "Posição de conta corrente", {"as_of": end}, cash[0][1])
    issue = re.search(rf"EMITIDO EM\s+({D})", text)
    header = upper(pages[0])
    branch = re.search(r"AGENCIA:\s*(\d+)", header)
    account = re.search(r"CONTA INVESTIMENTO:\s*(\d+)", header)
    owner_tax = re.search(r"CNPJ:\s*([\d./-]+)", header)
    fund = re.search(r"DETALHAMENTO\s*[›>]\s*(.+?)\s*-\s*(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})",
                     " ".join(t for t, _ in lot_pages[0]), re.I)
    notice = ("Posições de fundos BTG e lotes de aquisição: valores brutos, líquidos, impostos, cotas e datas históricas "
              "estão preservados no CSV e Excel. Posições e variação patrimonial não representam lançamentos bancários. "
              "OFX indisponível para este relatório.")
    return {"schema_version": "financial_events_v1", "document_type": "investment_statement", "origin": "BTG Pactual",
            "representation": "text", "evidence": ["btg_fund_positions_and_lots"], "events": [],
            "observations": observations, "validations": validations,
            "metadata": {"period_start": start, "period_end": end,
                         "source_bank_code": "208",
                         **({"source_bank_branch": branch[1]} if branch else {}),
                         **({"product_account": account[1]} if account else {}),
                         **({"owner_tax_id": owner_tax[1]} if owner_tax else {}),
                         **({"fund_name": fund[1], "fund_tax_id": fund[2]} if fund else {}),
                         **({"issue_date": day(issue[1], "%d/%m/%y")} if issue else {})},
            "notice": notice, "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
                "requires_destination_account": False, "supports_row_edits": False, "reason": notice}}
