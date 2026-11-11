"""Recognize complete, explicitly inactive XP and Bradesco statement templates."""
import re
from calendar import monthrange
from datetime import date

from app.application.normalization.text import normalize_upper_text as upper
from app.application.parsers.pdf.verified_statements import DATE, NUMBER, day, money

XP = "xp_empresas_extrato_conta_digital_pj_v1"
BRADESCO = "bradesco_net_empresa_explicit_no_activity_v1"
BB = 'bb_explicit_no_activity_v1'


def read_native_inactive(raw, pages):
    text = upper("\n".join(pages))
    if not any(marker in text for marker in (
            'EXTRATO INEXISTENTE PARA O PERIODO SELECIONADO', 'SMI.WSE.0004', 'A CONTA NAO FOI MOVIMENTADA')):
        return None
    from app.application.parsers.pdf.mercado_pago_inactive import _image_in_ledger
    from app.application.parsers.pdf.native_table_source import read_native_table_source

    source = read_native_table_source(raw)
    prepared = ["\n".join(line.text for line in page.lines) for page in source.pages]
    recognized = recognize_explicit_inactive(prepared)
    if recognized is None:
        return None
    lines = source.pages[0].lines
    boundaries = [line.bbox["top"] for line in lines if upper(line.text).startswith(
        ("EXTRATO DE:", "EXTRATO DA CONTA DIGITAL PJ", 'CONSULTAS - EXTRATO DE CONTA CORRENTE'))]
    if recognized[0] == BB:
        boundaries = [line.bbox['top'] for line in lines if upper(line.text).startswith('DT. BALANCETE')]
    if not boundaries or _image_in_ledger(raw, min(boundaries), 1):
        return None
    return prepared, source


def recognize_explicit_inactive(pages):
    if len(pages) != 1:
        return None
    lines = [upper(line).strip() for line in pages[0].splitlines() if line.strip()]
    text = " ".join(lines)
    if 'A CONTA NAO FOI MOVIMENTADA' in text:
        return _bb(lines, text)
    if "EXTRATO DA CONTA DIGITAL PJ" in text and "EXTRATO INEXISTENTE PARA O PERIODO SELECIONADO" in text:
        return _xp(lines, text)
    if all(key in text for key in ("BRADESCO", "NET EMPRESA", "EXTRATO MENSAL / POR PERIODO", "SMI.WSE.0004")):
        return _bradesco(lines, text)
    return None


def _bb(lines, text):
    declaration = r'\*{3} A CONTA NAO FOI MOVIMENTADA \*{3}'
    if ('CONSULTAS - EXTRATO DE CONTA CORRENTE' not in lines[:4]
            or not re.search(r'\b(?:OUVIDORIA BB|BANCO DO BRASIL)\b', text)
            or not re.search(r'AGENCIA \d{3,5}-[\dX]', text)
            or not re.search(r'CONTA CORRENTE [\d.]+-[\dX]', text)):
        return None
    periods = re.findall(r'PERIODO DO EXTRATO (\d{2})\s*/\s*(\d{4})', text)
    if len(periods) != 1:
        return None
    month, year = map(int, periods[0])
    try:
        start, end = date(year, month, 1), date(year, month, monthrange(year, month)[1])
    except ValueError:
        return None
    header = r'DT\. BALANCETE DT\. MOVIMENTO AG\. ORIGEM LOTE HISTORICO DOCUMENTO VALOR R\$ SALDO'
    body = re.search(header + rf' ({DATE}) 0000 00000 000 SALDO ANTERIOR ({NUMBER}) ([CD]) '
        + rf'({DATE}) 0000 00000 999 S\s*A\s*L\s*D\s*O ({NUMBER}) ([CD]) ' + declaration, text)
    if body is None or len(re.findall(declaration, text)) != 1:
        return None
    if day(body[1]) > start.isoformat() or day(body[4]) != end.isoformat():
        return None
    outside = text[:body.start()] + ' ' + text[body.end():]
    if re.search(DATE, re.sub(DATE + r' \d{2}:\d{2}:\d{2}', '', outside)):
        return None
    if re.search(NUMBER, re.sub(DATE, '', outside)):
        return None
    # Only known completion/contact footer lines may follow the declaration.
    after = False
    for line in lines:
        if re.fullmatch(declaration, line):
            after = True
        elif after and not (re.fullmatch(r'-+', line) or line.startswith((
                'TRANSACAO EFETUADA COM SUCESSO POR:', 'SERVICO DE ATENDIMENTO AO CONSUMIDOR',
                'OUVIDORIA BB', 'PARA DEFICIENTES AUDITIVOS'))):
            return None
    opening = money(body[2]) * (-1 if body[3] == 'D' else 1)
    closing = money(body[5]) * (-1 if body[6] == 'D' else 1)
    if opening != closing:
        return None
    return BB, {'verified_opening_balance': float(opening), 'verified_closing_balance': float(closing),
                'statement_period_start': start.isoformat(), 'statement_period_end': end.isoformat(),
                'financial_controls_validated': 1, 'printed_balance_checks': 2, 'document_coverage_validated': 1,
                'balance_validation_status': 'explicit_no_activity_period_balance'}


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
