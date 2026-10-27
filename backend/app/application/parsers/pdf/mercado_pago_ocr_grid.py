"""Mercado Pago OCR columns with printed per-row and whole-statement controls."""
import re
from dataclasses import replace
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.followup_ocr import _headers, _source
from app.application.parsers.pdf.statement_review import financial_mismatch
from app.application.parsers.pdf.verified_statements import NUMBER, VerifiedStatement, _row, day, fail, money
from app.application.sweep_statement_detection import MERCADO_PAGO

_CURRENCY = re.compile(rf"R\$\s*(?P<duplicate>\$)?\s*(?P<number>[+-]?\s*{NUMBER})(?:\s+(?P<annotation>[0O]))?", re.I)


def parse_mercado_pago_ocr_grid(source):
    headers = ("DATA", "DESCRICAO", "ID DA OPERACAO", "VALOR", "SALDO")
    if not source.pages or not _headers(source.pages[0], headers):
        return None
    text = upper("\n".join(line.text for page in source.pages for line in page.lines))
    period = re.search(r"PERIODO:\s*DE (\d{2}-\d{2}-\d{4}) AL (\d{2}-\d{2}-\d{4})", text)
    if period is None:
        fail()
    start, end = [day(value, "%d-%m-%Y") for value in period.groups()]
    def summary(label):
        matches = re.findall(rf"{label}:\s*R\$\s*([+-]?\s*{NUMBER})", text)
        if len(matches) != 1:
            fail()
        return money(re.sub(r"\s+", "", matches[0]))
    opening, closing = summary("SALDO INICIAL"), summary("SALDO FINAL")
    inflows, outflows = abs(summary("ENTRADAS")), abs(summary("SAIDAS"))
    if start > end:
        fail()
    rows, running, checks = [], opening, 0
    for page in source.pages:
        columns = _headers(page, headers)
        if columns is None:
            fail("Uma página Mercado Pago não possui as colunas necessárias.")
        header_y = max(line.bbox["top"] for line in page.lines if upper(line.text) in headers)
        dates = sorted([line for line in page.lines if line.bbox and line.bbox["top"] > header_y
                        and line.bbox["left"] < columns["DESCRICAO"] - .02
                        and re.fullmatch(r"\d{2}-\d{2}-\d{4}", line.text)], key=lambda line: line.bbox["top"])
        cash = [line for line in page.lines if line.bbox and line.bbox["top"] > header_y
                and line.bbox["left"] >= columns["VALOR"] - .08 and "R$" in line.text]
        if not dates or any(not _CURRENCY.fullmatch(line.text.strip()) for line in cash):
            fail("Um valor Mercado Pago não está legível ou não possui data associada.")
        used = set()
        for index, anchor in enumerate(dates):
            low = (dates[index-1].bbox["top"] + anchor.bbox["top"])/2 if index else header_y+.008
            high = (anchor.bbox["top"] + dates[index+1].bbox["top"])/2 if index+1 < len(dates) else 1
            cells = [line for line in page.lines if line.bbox and low <= line.bbox["top"] < high]
            values = sorted([line for line in cash if line in cells], key=lambda line: line.bbox["left"])
            references = [line for line in cells if columns["ID DA OPERACAO"]-.02 <= line.bbox["left"] < columns["VALOR"]-.08
                          and re.fullmatch(r"\d{6,}", line.text)]
            history = sorted([line for line in cells if columns["DESCRICAO"]-.02 <= line.bbox["left"]
                              < columns["ID DA OPERACAO"]-.02], key=lambda line: (line.bbox["top"], line.bbox["left"]))
            if len(values) != 2 or len(references) != 1 or not history:
                fail("Não foi possível associar todas as colunas de uma operação Mercado Pago.")
            boundary = (columns["VALOR"] + columns["SALDO"])/2
            if (values[0].bbox["left"] + values[0].bbox["width"]/2 >= boundary
                    or values[1].bbox["left"] + values[1].bbox["width"]/2 <= boundary):
                fail("As colunas de valor e saldo Mercado Pago não estão separadas.")
            date = day(anchor.text, "%d-%m-%Y")
            if not start <= date <= end or rows and date < rows[-1].transaction.date:
                fail()
            parsed = [_CURRENCY.fullmatch(line.text.strip()) for line in values]
            amount, balance = [money(re.sub(r"\s+", "", match["number"])) for match in parsed]
            annotation = any(match["duplicate"] or match["annotation"] for match in parsed)
            if annotation and running+amount != balance:
                fail("A marca junto ao valor Mercado Pago não pode ser confirmada pelos saldos impressos.")
            if running+amount != balance:
                financial_mismatch("Os movimentos Mercado Pago não fecham com os saldos impressos.")
            row = _source(_row(page.page_number, anchor.line_index, date, " ".join(line.text for line in history),
                               amount, balance, references[0].text), page, cells)
            if annotation:
                row = replace(row, warnings=[*row.warnings, "ocr_balance_annotation_confirmed_by_controls"])
            rows.append(row)
            running = balance
            used.update(line.id for line in values)
            checks += 1
        if used != {line.id for line in cash}:
            fail("Há valores Mercado Pago sem operação associada.")
    incoming = sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount > 0), Decimal(0))
    outgoing = -sum((Decimal(str(row.transaction.amount)) for row in rows if row.transaction.amount < 0), Decimal(0))
    if running != closing or incoming != inflows or outgoing != outflows:
        financial_mismatch("Os totais Mercado Pago não correspondem às movimentações impressas.")
    return VerifiedStatement(rows, MERCADO_PAGO, opening, closing, printed_balance_checks=checks+3, candidate_rows=len(rows))
