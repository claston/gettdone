"""Recognize complete, explicitly inactive XP and Bradesco statement templates."""
import re

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, day, money

XP = "xp_empresas_extrato_conta_digital_pj_v1"
BRADESCO = "bradesco_net_empresa_explicit_no_activity_v1"


def recognize_explicit_inactive(pages):
    if len(pages) != 1:
        return None
    lines = [upper(line).strip() for line in pages[0].splitlines() if line.strip()]
    text = " ".join(lines)
    if "EXTRATO DA CONTA DIGITAL PJ" in text and "EXTRATO INEXISTENTE PARA O PERIODO SELECIONADO" in text:
        return _xp(lines, text)
    if all(key in text for key in ("BRADESCO", "NET EMPRESA", "EXTRATO MENSAL / POR PERIODO", "SMI.WSE.0004")):
        return _bradesco(lines, text)
    return None


def _xp(lines, text):
    if not all(key in text for key in ("AGENCIA", "CONTA", "FALE CONOSCO")):
        return None
    body = re.search(rf"EXTRATO DA CONTA DIGITAL PJ PERIODO DE VISUALIZACAO\s*DE\s+({DATE})\s+A\s+({DATE}) "
        r"EXTRATO INEXISTENTE PARA O PERIODO SELECIONADO FALE CONOSCO\b", text)
    if body is None or day(body[1]) > day(body[2]):
        return None
    header = text[:body.start()]
    labels = ("SALDO INICIAL", "ENTRADAS", "SAIDAS", "SALDO FINAL")
    amounts = []
    for label in labels:
        matches = re.findall(rf"{label}\s*R\$\s*(-?{NUMBER})", header)
        if len(matches) != 1:
            amounts = []
            break
        amounts.append(money(matches[0]))
    if not amounts:
        match = re.search(r"SALDO INICIAL ENTRADAS SAIDAS SALDO FINAL\s+"
            + r"\s+".join([rf"R\$\s*(-?{NUMBER})"] * 4) + r"$", header.strip())
        if match is None:
            return None
        amounts = list(map(money, match.groups()))
    if len(re.findall(rf"R\$\s*-?{NUMBER}", text)) != 4:
        return None
    opening, inflows, outflows, closing = amounts
    if inflows != 0 or outflows != 0 or opening != closing:
        return None
    return XP, {"verified_opening_balance": float(opening), "verified_closing_balance": float(closing),
                "financial_controls_validated": 1, "printed_balance_checks": 4}


def _bradesco(lines, text):
    declaration = "NAO HA LANCAMENTOS PARA ESTE TIPO DE EXTRATO. (SMI.WSE.0004)"
    if lines.count(declaration) != 2 or re.findall(r"FOLHA\s+(\d+)/(\d+)", text) != [("1", "1")]:
        return None
    required = ("TOTAL DISPONIVEL (R$)", "INVESTIMENTO SEM BAIXA AUTOMATICA (R$)", "(A+B)", "ULTIMOS LANCAMENTOS")
    if not all(key in text for key in required):
        return None
    periods = [i for i, line in enumerate(lines) if re.search(rf"^EXTRATO DE:.*ENTRE\s+({DATE})\s+E\s+({DATE})$", line)]
    if len(periods) != 1:
        return None
    period = re.search(rf"ENTRE\s+({DATE})\s+E\s+({DATE})$", lines[periods[0]])
    if day(period[1]) > day(period[2]):
        return None
    for line in lines[periods[0] + 1:]:
        if line not in (declaration, "ULTIMOS LANCAMENTOS") and not re.fullmatch(
                rf"OS DADOS ACIMA TEM COMO BASE {DATE} (?:AS )?\d{{2}}H\d{{2}} E ESTAO SUJEITOS A ALTERACOES\.", line):
            return None
    values = [money(line) for line in lines[:periods[0]] if re.fullmatch(NUMBER, line)]
    if len(values) != 3 or values[0] + values[1] != values[2]:
        return None
    # Available funds are a snapshot, not the queried period's ledger balances.
    return BRADESCO, {"financial_controls_validated": 0, "balance_validation_status": "not_available"}
