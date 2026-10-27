"""Confirm a blank native Mercado Pago ledger from its printed controls."""
import re
from contextlib import closing

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.native_unicred_grid import _PDFIUM_LOCK
from app.application.parsers.pdf.verified_statements import MONEY, NUMBER, VerifiedStatement, day, money
from app.application.sweep_statement_detection import MERCADO_PAGO, identify_sweep_statement


def _image_in_ledger(raw, top, bottom):
    import pypdfium2 as pdfium

    with _PDFIUM_LOCK, pdfium.PdfDocument(raw) as document, closing(document[0]) as page:
        height = page.get_height()
        # A raster or unexamined form in the blank area could contain movements.
        for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE, pdfium.raw.FPDF_PAGEOBJ_FORM], max_depth=1):
            _, lower, _, upper_bound = obj.get_bounds()
            if 1 - upper_bound / height < bottom and 1 - lower / height > top:
                return True
    return False


def parse_mercado_pago_inactive(raw, source):
    if source.provider != "native_pdfium_text" or len(source.pages) != 1:
        return None
    lines = source.pages[0].lines
    text = "\n".join(upper(line.text) for line in lines)
    if identify_sweep_statement(text) != MERCADO_PAGO:
        return None
    periods = re.findall(r"PERIODO:\s*DE (\d{2}-\d{2}-\d{4}) AL (\d{2}-\d{2}-\d{4})", text)
    if len(periods) != 1 or day(periods[0][0], "%d-%m-%Y") > day(periods[0][1], "%d-%m-%Y"):
        return None
    if len(re.findall(r"AGENCIA:\s*\d+\s+CONTA:\s*[\d.-]+", text)) != 1:
        return None
    controls = {}
    for label in ("SALDO INICIAL", "SALDO FINAL", "ENTRADAS", "SAIDAS"):
        matches = re.findall(rf"^{label}:\s*R\$\s*([+-]?{NUMBER})$", text, re.M)
        if len(matches) != 1:
            return None
        controls[label] = money(matches[0])
    if (len(MONEY.findall(text)) != 4 or controls["ENTRADAS"] != 0 or controls["SAIDAS"] != 0
            or controls["SALDO INICIAL"] != controls["SALDO FINAL"]):
        return None
    headers = []
    for label in ("DATA", "DESCRICAO", "ID DA OPERACAO", "VALOR", "SALDO"):
        matches = [line for line in lines if upper(line.text) == label]
        if len(matches) != 1:
            return None
        headers.append(matches[0])
    if (max(line.bbox["top"] for line in headers) - min(line.bbox["top"] for line in headers) > .01
            or any(left.bbox["left"] + left.bbox["width"] >= right.bbox["left"]
                   for left, right in zip(headers, headers[1:]))):
        return None
    headings = [line for line in lines if upper(line.text) == "DETALHE DOS MOVIMENTOS"]
    footers = [line for line in lines if re.fullmatch(r"DATA DE GERACAO: \d{2}-\d{2}-\d{4}", upper(line.text))]
    counters = [line for line in lines if re.fullmatch(r"\d+/\d+", line.text)]
    if len(headings) != 1 or len(footers) != 1 or len(counters) != 1 or counters[0].text != "1/1":
        return None
    generation = upper(footers[0].text).split(":", 1)[1].strip()
    if (day(generation, "%d-%m-%Y") < day(periods[0][1], "%d-%m-%Y")
            or len(re.findall(r"\b\d{2}-\d{2}-\d{4}\b", text)) != 3):
        return None
    top = max(line.bbox["top"] + line.bbox["height"] for line in headers)
    bottom = footers[0].bbox["top"]
    if not (headings[0].bbox["top"] < headers[0].bbox["top"] < top < bottom < counters[0].bbox["top"]):
        return None
    if any(line not in headers and line.bbox["top"] < bottom
           and line.bbox["top"] + line.bbox["height"] > top for line in lines):
        return None
    if _image_in_ledger(raw, top, bottom):
        return None
    return VerifiedStatement([], MERCADO_PAGO, controls["SALDO INICIAL"], controls["SALDO FINAL"], printed_balance_checks=4)
