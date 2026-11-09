"""Positive evidence of a native empty ledger, separate from investment snapshots."""
import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.mercado_pago_inactive import _image_in_ledger
from app.application.parsers.pdf.native_table_source import native_header_columns
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, VerifiedStatement, money


def parse_inactive_cash_table(raw, source):
    if len(source.pages) != 1:
        return None
    page = source.pages[0]
    text = upper(" ".join(line.text for line in page.lines))
    if "A CONTA NAO FOI MOVIMENTADA" in text:
        headers = native_header_columns(page, ("HISTORICO", "VALOR R$", "SALDO"))
        if not headers:
            return None
        heading = [w for w in page.words if upper(w.text) == "HISTORICO"]
        endings = [line for line in page.lines if upper(line.text).startswith("INVEST. RESGATE AUTOM.")]
        if len(heading) != 1 or len(endings) != 1:
            return None
        top, bottom = heading[0].bbox["top"]+heading[0].bbox["height"], endings[0].bbox["top"]
        body = [line for line in page.lines if top < line.bbox["top"] < bottom]
        dates = [line for line in body if re.fullmatch(DATE, line.text)]
        histories = [line for line in body if re.fullmatch(r"(?:\d+\s+)*SALDO ANTERIOR",upper(line.text))]
        values = [line for line in body if re.fullmatch(rf"({NUMBER})\s*[CD]", line.text)]
        if (len(dates) != 1 or len(histories) != 1
                or len(values) != 1 or _image_in_ledger(raw,top,bottom)):
            return None
        known = {line.id for line in (*dates, *histories, *values)}
        if any(line.id not in known and not re.fullmatch(r"0+", line.text)
               and "A CONTA NAO FOI MOVIMENTADA" not in upper(line.text) for line in body):
            return None
        value = re.fullmatch(rf"({NUMBER})\s*([CD])",values[0].text)
        balance = money(value[1])*(-1 if value[2] == "D" else 1)
        return VerifiedStatement([],"banco_do_brasil_autoatendimento_cd_ocr_v1",balance,balance,printed_balance_checks=1)
    if "SICREDI" not in text or "PERIODO: DE" not in text:
        return None
    columns = native_header_columns(page,("DEBITO","CREDITO","SALDO"))
    headings = [w for w in page.words if upper(w.text) == "DEBITO"]
    endings = [line for line in page.lines if upper(line.text).startswith("SALDO ATUAL")]
    if not columns or len(headings) != 1 or len(endings) != 1:
        return None
    top, bottom = headings[0].bbox["top"]+headings[0].bbox["height"], endings[0].bbox["top"]
    body = " ".join(line.text for line in page.lines if top < line.bbox["top"] < bottom)
    if re.sub(r"[\s=*/]", "", upper(body)) != "SALDOANTERIOR0,00" or _image_in_ledger(raw,top,bottom):
        return None
    closing = [line for line in page.lines if abs(line.bbox["top"]-bottom)<.004
               and line.bbox["left"] > columns["SALDO"]-.02 and line.text == "0,00"]
    if len(closing) != 1:
        return None
    return VerifiedStatement([],"sicredi_matricial_paisagem_conta_corrente_v1",Decimal(0),Decimal(0),printed_balance_checks=2)
