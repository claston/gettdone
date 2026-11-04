"""BTG cash-only investment accounts; portfolio positions remain observations."""

import re
from decimal import Decimal

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.financial_reports import _check, _event
from app.application.parsers.pdf.verified_statements import NUMBER, day, fail, money

_DATE = r"\d{2}/\d{2}/\d{2}"
_VALUE = rf"(?:[+-]?{NUMBER}|-)"
_TITLE = "CONTA CORRENTE - MOVIMENTACAO"


def _one(items):
    if len(items) != 1:
        fail("Os campos da conta investimento BTG não podem ser associados de forma inequívoca.")
    return items[0]


def _amount(token):
    return None if token == "-" else money(token)


def parse_btg_investment_cash(pages):
    text = upper(" ".join(pages))
    if (not re.search(r"DISTRIBUICAO DA CARTEIRA\s+CONTA CORRENTE\s+100[.,]00%", text)
            or "POSICAO PORTFOLIO DE FUNDOS" in text or re.search(r"DETALHAMENTO\s*[›>]", text)):
        fail("Esta variante BTG exige uma carteira integralmente em conta corrente; "
             "posições de outros produtos precisam de leitura própria.")
    period = _one(list(set(re.findall(rf"PERIODO DE\s+({_DATE})\s+(?:A|ATE)\s+({_DATE})", text))))
    start, end = (day(value, "%d/%m/%y") for value in period)
    if start > end:
        fail()
    lines = [(line.strip(), {"page": page, "line": number}) for page, content in enumerate(pages, 1)
             for number, line in enumerate(content.splitlines(), 1) if line.strip()]
    summary_pattern = r"\s+".join([rf"({_VALUE})"] * 4)
    summary = _one(re.findall(r"\bCONTA CORRENTE\s+" + summary_pattern + r"(?=\s+TOTAL\b)", text))
    total = _one(re.findall(r"\bTOTAL\s+" + summary_pattern + r"(?=\s+CONTA CORRENTE POSICAO\b)", text))
    position = _one(re.findall(rf"CONTA CORRENTE POSICAO\s+DATA\s+VALOR FINANCEIRO R\$\s+({_DATE})\s+({_VALUE})(?![\d,.])", text))
    validations = [_check("btg_cash_summary_total", tuple(map(_amount, summary)), tuple(map(_amount, total)))]
    if day(position[0], "%d/%m/%y") != end or _amount(position[1]) is None:
        fail("A data ou o valor da posição final BTG não pode ser confirmado.")
    closing_position = _amount(position[1])
    if any(_amount(value) is None for value in summary[2:]):
        fail("A posição final da conta investimento BTG não está legível.")
    validations.extend(_check("btg_cash_position", _amount(value), closing_position) for value in summary[2:])

    begin = _one([index for index, (line, _) in enumerate(lines) if upper(line) == _TITLE])
    # The exact section headers delimit records; a missing field is never
    # filled from totals or taken from a neighbouring financial record.
    header = ["DATA", "DESCRICAO", "MOVIMENTACAO R$", "SALDO CONTA INVESTIMENTOS R$"]
    if [upper(line) for line, _ in lines[begin+1:begin+5]] != header:
        fail("O cabeçalho de movimentos BTG não está completo.")
    cursor, packets = begin+5, []
    while cursor < len(lines) and not upper(lines[cursor][0]).startswith("TOTAL DE"):
        first, ref = lines[cursor]
        if not re.match(rf"^{_DATE}(?:\s|$)", first):
            fail("Uma linha BTG está sem sua data legível.")
        packet, refs = [first], [ref]
        cursor += 1
        while (cursor < len(lines) and not re.match(rf"^{_DATE}(?:\s|$)", lines[cursor][0])
               and not upper(lines[cursor][0]).startswith("TOTAL DE")):
            packet.append(lines[cursor][0])
            refs.append(lines[cursor][1])
            cursor += 1
        packets.append((" ".join(packet), refs))
    if len(packets) < 3:
        fail("Os movimentos e controles da conta investimento BTG estão incompletos.")
    opening = re.fullmatch(rf"({_DATE})\s+SALDO ANTERIOR\s+({_VALUE})", upper(packets[0][0]))
    closing = re.fullmatch(rf"({_DATE})\s+SALDO FINAL \+ RENDIMENTO PROVISIONADO DE SALDO REMUNERADO\s+({_VALUE})",
                           upper(packets[-1][0]))
    if not opening or not closing or day(opening[1], "%d/%m/%y") != start or day(closing[1], "%d/%m/%y") != end:
        fail("Os limites de data dos movimentos BTG não conferem com o período impresso.")
    opening_value, closing_value = _amount(opening[2]), _amount(closing[2])
    if closing_value is None:
        fail()
    validations.append(_check("btg_cash_ledger_closing", closing_value, closing_position, refs=packets[-1][1]))
    if opening_value is not None:
        validations.extend(_check("btg_cash_summary_opening", _amount(value), opening_value) for value in summary[:2]
                           if _amount(value) is not None)
    events, running, previous_date = [], opening_value, start
    for raw, refs in packets[1:-1]:
        row = re.fullmatch(rf"({_DATE})\s+(.+?)\s+([+-]?{NUMBER})\s+([+-]?{NUMBER})", raw)
        if not row or re.search(NUMBER, row[2]) or re.search(r"[\d?]{2}/[\d?]{2}/[\d?]{2,4}", row[2]):
            fail("Um movimento BTG está sem descrição, valor com sinal ou saldo legível.")
        date = day(row[1], "%d/%m/%y")
        if not previous_date <= date <= end:
            fail("As datas da conta investimento BTG não conferem.")
        amount, balance = money(row[3]), money(row[4])
        if running is not None:
            validations.append(_check("btg_cash_running_balance", running+amount, balance, refs=refs))
        running, previous_date = balance, date
        events.append(_event("investment_movement", dates={"transaction": date},
            amounts={"movement": format(amount, ".2f"), "balance": format(balance, ".2f")}, primary="movement",
            cash_effect=format(amount, ".2f"), reference=None, description=row[2], refs=refs))
    validations.append(_check("btg_cash_last_balance", running, closing_value, refs=packets[-1][1]))
    tail = upper(" ".join(line for line, _ in lines[cursor:cursor+6]))
    totals = re.fullmatch(rf"TOTAL DE\s+([+-]?{NUMBER})\s+CREDITOS\s+TOTAL DE\s+([+-]?{NUMBER})\s+DEBITOS", tail)
    if not totals:
        fail("Os totais impressos de créditos e débitos BTG não estão legíveis.")
    for sign, printed in ((1, totals[1]), (-1, totals[2])):
        actual = sum((Decimal(event["amount"]) for event in events if Decimal(event["amount"])*sign > 0), Decimal(0))
        validations.append(_check("btg_cash_credits" if sign == 1 else "btg_cash_debits", actual, money(printed)))
    # Reject a second cash section or financial row after the supposed close.
    for line, _ in lines[cursor+6:]:
        if re.match(rf"^{_DATE}(?:\s|$)", line) or upper(line) == _TITLE:
            fail("Há outra linha financeira BTG após o fechamento da seção.")
    observations = [{"observation_type": kind, "dates": {"as_of": date},
                     "amounts": {"balance": format(value, ".2f")} if value is not None else {},
                     "attributes": {"printed_value": token}, "source_refs": refs}
                    for kind, date, value, token, refs in (
                        ("opening_position", start, opening_value, opening[2], packets[0][1]),
                        ("closing_position", end, closing_value, closing[2], packets[-1][1]))]
    notice = ("Movimentos da conta investimento BTG: valores, sinais, saldos e posições preservados no CSV e Excel. "
              "Um traço no saldo inicial permanece sem valor informado. OFX indisponível para este relatório de investimentos.")
    return {"schema_version": "financial_events_v1", "document_type": "investment_statement", "origin": "BTG Pactual",
            "representation": "text", "evidence": ["btg_investment_cash_ledger"], "events": events,
            "observations": observations, "validations": validations,
            "metadata": {"period_start": start, "period_end": end, "source_bank_code": "208",
                         "opening_balance": format(opening_value, ".2f") if opening_value is not None else None,
                         "closing_balance": format(closing_value, ".2f")},
            "notice": notice, "export": {"available_formats": ["csv", "xlsx"], "ofx_profile": None,
                "requires_destination_account": False, "supports_row_edits": False, "reason": notice}}
