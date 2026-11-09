"""BB fund positions, operations, quotes and monthly controls, never bank cash."""
import re
from decimal import Decimal

from app.application.parsers.pdf.financial_reports import _check
from app.application.parsers.pdf.period_report_source import (
    NUMBER,
    account,
    blocks,
    continuity,
    decimal,
    document,
    observation,
    refs,
)
from app.application.parsers.pdf.sicredi_term_deposits import month
from app.application.parsers.pdf.verified_statements import DATE, day, fail

MONTHS = 'JANEIRO FEVEREIRO MARCO ABRIL MAIO JUNHO JULHO AGOSTO SETEMBRO OUTUBRO NOVEMBRO DEZEMBRO'.split()
SUMMARY = {'SALDO ANTERIOR': 'opening', 'APLICACOES (+)': 'applications', 'RESGATES (-)': 'redemptions',
           'RENDIMENTO BRUTO (+)': 'gross_income', 'IMPOSTO DE RENDA (-)': 'withheld_irrf', 'IOF (-)': 'withheld_iof',
           'RENDIMENTO LIQUIDO': 'net_income', 'SALDO ATUAL =': 'closing'}
HEADERS = [('Valor', 'value'), ('Valor IR', 'withheld_irrf'), ('Prej. Comp.', 'compensated_loss'),
           ('Valor IOF', 'withheld_iof'), ('Quantidade cotas', 'quantity'), ('Valor cota', 'unit_quote'),
           ('Saldo cotas', 'balance_quantity')]


def _columns(row):
    tokens = [w.text.upper() for w in row.words]
    result = {}
    for label, field in HEADERS:
        parts = label.upper().split()
        choices = [i for i in range(len(tokens)) if tokens[i:i+len(parts)] == parts]
        if field == 'value':
            choices = choices[:1]
        if len(choices) != 1:
            fail('As colunas do extrato mensal de fundos BB não estão legíveis.')
        end = row.words[choices[0]+len(parts)-1].bbox
        result[field] = end['left']+end['width']
    return result


def parse_bb_monthly_funds(rows):
    sections = blocks(rows, lambda t: 'EXTRATOS - INVESTIMENTOS FUNDOS - MENSAL' in t,
                      lambda rs: any(r.normalized.startswith('SALDO ATUAL =') for r in rs))
    observations, checks, prior = [], [], {}
    for block, section in enumerate(sections, 1):
        text = '\n'.join(r.normalized for r in section)
        periods = set(re.findall(r'(?:MES/ANO REFERENCIA|MES/ANO DE REFERENCIA)\s+([A-Z]+)/(\d{4})', text))
        if len(periods) > 1:
            fail('Há meses de referência diferentes sem separação de bloco de fundos BB.')
        period = None
        if periods:
            name, year = next(iter(periods))
            if name in MONTHS:
                period = f'{MONTHS.index(name)+1:02d}/{year}'
        if period is None:
            checks.append(_check('bb_statement_month_unconfirmed', False, True, refs=refs(section)))
        source_account = account(section)
        fund, columns, mode, summary, fund_rows, summaries, items = None, None, None, {}, [], [], []

        def finish():
            if fund is None:
                return
            if set(summary) != set(SUMMARY.values()) or not fund_rows:
                fail('Um fundo está sem posições ou resumo completo; a extração parcial não é permitida.')
            checks.append(_check('bb_fund_net_income', summary['gross_income']-summary['withheld_irrf']-summary['withheld_iof'],
                                 summary['net_income'], refs=refs(fund_rows)))
            checks.append(_check('bb_fund_month_balance', summary['opening']+summary['applications']-summary['redemptions']+
                                 summary['net_income'], summary['closing'], refs=refs(fund_rows)))
            for label, field in [('SALDO ANTERIOR', 'opening'), ('SALDO ATUAL', 'closing')]:
                positions = [o for o in items if o['attributes']['label'].upper() == label]
                if len(positions) != 1:
                    fail('As posições inicial e final do fundo não estão associadas ao resumo.')
                checks.append(_check('bb_position_'+field, Decimal(positions[0]['amounts']['value']), summary[field],
                                     refs=positions[0]['source_refs']))
            for label, field in [('APLICA', 'applications'), ('RESGATE', 'redemptions')]:
                movements = [o for o in items if o['attributes']['label'].upper().startswith(label)]
                checks.append(_check('bb_printed_'+field, sum((Decimal(o['amounts']['value']) for o in movements), Decimal(0)),
                                     summary[field], refs=refs(fund_rows)))
            if source_account and period:
                continuity(checks, prior, (source_account, fund), month(period), month(period),
                           summary['opening'], summary['closing'], refs(fund_rows))
            summaries.append(summary.copy())

        for row in section:
            t = row.normalized
            if fund is None and re.fullmatch(rf'{DATE}\s+\d{{2}}:\d{{2}}:\d{{2}}', t):
                continue  # report emission timestamp, not a financial row
            title = re.fullmatch(r'(.+?)\s*-\s*CNPJ:\s*([\d./-]+)', row.text, re.I)
            if title:
                finish()
                fund = title[1].strip()
                attrs = {'fund_name': fund, 'fund_tax_id': title[2], 'statement_month': period,
                         'source_account_number': source_account, 'block': block}
                columns, mode, summary, fund_rows, items = None, None, {}, [], []
                continue
            if 'HISTORICO' in t and 'QUANTIDADE' in t and 'VALOR' in t:
                if fund is None:
                    fail('Uma tabela de fundos está sem identificação do ativo.')
                columns, mode = _columns(row), 'table'
                continue
            if t == 'RESUMO DO MES':
                mode = 'summary'
                continue
            if t == 'VALOR DA COTA':
                mode = 'quote'
                continue
            if t == 'RENTABILIDADE':
                mode = 'performance'
                continue
            if re.match(r'^\d{2}/', t):
                if fund is None or columns is None:
                    fail('Uma linha de fundos não está associada ao ativo e às colunas originais.')
                date_token = row.words[0].text
                date = day(date_token)
                if mode == 'quote':
                    if len(row.words) != 2:
                        fail('Uma cotação de fundo está ilegível.')
                    quote = str(decimal(row.words[1].text, places=None))
                    observations.append(observation('investment_fund_quote', row, dates={'as_of': date},
                        attributes={**attrs, 'unit_quote': quote}))
                    continue
                if mode != 'table':
                    fail('Há uma linha de fundo fora da tabela reconhecida.')
                financial = [w for w in row.words[1:] if re.search(r'\d', w.text)]
                if not financial:
                    fail('Uma posição de fundo está sem valor legível.')
                values = {}
                for word in financial:
                    edge = word.bbox['left']+word.bbox['width']
                    field = min(columns, key=lambda key: abs(columns[key]-edge))
                    if field in values or abs(columns[field]-edge) > .045:
                        fail('Um valor ou quantidade de cotas está fora da coluna identificada.')
                    values[field] = str(decimal(word.text, places=None if field in {'quantity', 'unit_quote', 'balance_quantity'} else 2))
                history = ' '.join(w.text for w in row.words[1:] if w not in financial)
                if not history or 'value' not in values:
                    fail('Uma posição de fundo está sem histórico ou valor.')
                if not any(marker in history.upper() for marker in ('SALDO', 'APLICA', 'RESGATE', 'COBRAN', 'IR ', 'IOF', 'TRANSFER')):
                    fail('O relatório contém uma operação de fundo ainda não reconhecida.')
                amount_fields = {k: v for k, v in values.items() if k not in {'quantity', 'unit_quote', 'balance_quantity'}}
                kind = 'investment_fund_position' if 'SALDO' in history.upper() else 'investment_fund_movement'
                item = observation(kind, row, amounts=amount_fields, dates={'as_of': date},
                    attributes={**attrs, 'label': history, **{k: v for k, v in values.items() if k not in amount_fields}})
                observations.append(item)
                items.append(item)
                fund_rows.append(row)
                continue
            values = re.findall(NUMBER, row.text)
            if mode == 'summary' and values:
                label = re.sub(NUMBER, '', t).strip()
                field = SUMMARY.get(label)
                if field is None or len(values) != 1 or field in summary:
                    fail('Um campo do resumo de fundos BB está ausente, repetido ou ilegível.')
                value = decimal(values[0])
                summary[field] = value
                observations.append(observation('investment_fund_summary', row, amounts={field: str(value)}, attributes=attrs))
                fund_rows.append(row)
                continue
            if mode == 'performance' and re.match(r'^(NO MES|NO ANO|ULTIMOS 12 MESES)\b', t):
                match = re.fullmatch(rf'(NO MES|NO ANO|ULTIMOS 12 MESES)\s+({NUMBER})', t)
                if not match:
                    fail('Uma rentabilidade de fundo está ilegível.')
                observations.append(observation('investment_fund_performance', row, attributes={**attrs,
                    'label': match[1], 'rate_percent': str(decimal(match[2], places=None))}))
                continue
            if values or re.search(r'\d+[?,]\d', t):
                fail('Há uma linha financeira de fundos BB não reconhecida.')
        finish()
        if not summaries:
            fail('Não foi possível identificar os fundos e suas posições.')
        if not source_account:
            checks.append(_check('source_account_unconfirmed', False, True, refs=refs(section)))
        for page in {r.page.page_number for r in section}:
            if not any(ref['page'] == page for obs in observations for ref in obs['source_refs']):
                fail('Uma página posterior está sem posições ou campos financeiros reconhecidos.')
    return document('Banco do Brasil', 'bb_monthly_fund_positions', observations, checks, {'blocks': len(sections)})
