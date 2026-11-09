"""Monthly term-deposit aggregates are observations, without invented cash dates."""
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
from app.application.parsers.pdf.verified_statements import DATE, day, fail

FIELDS = ('applications', 'redemptions', 'paid_income', 'withheld_irrf', 'withheld_iof',
          'month_income', 'provisioned_income', 'principal')


def month(text):
    if not re.fullmatch(r'(0[1-9]|1[0-2])/\d{4}', text):
        fail('O mês/ano do investimento está ilegível.')
    return int(text[3:])*12+int(text[:2])-1


def parse_sicredi_term_deposits(rows):
    sections = blocks(rows, lambda t: 'EXTRATO' in t and 'DEPOSITO A PRAZO' in t,
                      lambda rs: any('LIQUIDO' in r.normalized and re.search(NUMBER, r.text) for r in rs))
    observations, checks, prior = [], [], {}
    for block, section in enumerate(sections, 1):
        source_account = account(section)
        product_rows = [r for r in section if 'PRODUTO' in r.normalized and 'SICREDINVEST' in r.normalized]
        product = 'SICREDINVEST AUTOMATICO' if any('AUTOMATICO' in r.normalized for r in product_rows) else 'SICREDINVEST'
        attrs = {'product': product, 'source_account_number': source_account, 'block': block}
        monthly, totals, opening, accrued, summary, summary_refs = [], None, None, None, {}, []
        opening_date, as_of = None, None
        table, withdrawal = False, False
        seen_pages = set()
        for row in section:
            text = row.normalized
            values = re.findall(NUMBER, row.text)
            if re.match(rf'^{DATE}\s+SISTEMA SICREDI\b', text):
                continue
            if 'MOVIMENTACOES' in text and 'TRIBUTACAO' in text and not values:
                continue  # parent headings above the eight monthly columns
            if 'MES/ANO' in text:
                table = True
                continue
            if re.match(r'^\d{2}/\d{4}\b', text):
                match = re.fullmatch(rf'(\d{{2}}/\d{{4}})\s+({NUMBER}(?:\s+{NUMBER}){{7}})', text)
                if not table or not match:
                    fail('Uma linha mensal SICREDINVEST está sem mês ou algum dos oito valores impressos.')
                month(match[1])
                amounts = dict(zip(FIELDS, (str(decimal(v)) for v in match[2].split()), strict=True))
                item = observation('investment_monthly_summary', row, amounts=amounts,
                                   attributes={**attrs, 'statement_month': match[1], 'label': 'Resumo mensal do investimento'})
                monthly.append(item)
                observations.append(item)
                seen_pages.add(row.page.page_number)
                continue
            if text.startswith('TOTAIS'):
                if len(values) != 5 or totals is not None:
                    fail('Os totais de aplicações, resgates, rendimentos e impostos estão incompletos.')
                totals = dict(zip(FIELDS[:5], (decimal(v) for v in values), strict=True))
                observations.append(observation('investment_period_totals', row,
                    amounts={k: str(v) for k, v in totals.items()}, attributes=attrs))
                continue
            if 'POSICAO PARA SAQUE' in text or re.match(r'POSICAO EM\b', text):
                withdrawal = True
                dates = re.findall(DATE, text)
                if dates:
                    as_of = day(dates[0])
                continue
            if 'SALDO ANTERIOR' in text or ('RENDIMENTOS PROVISIONADOS' in text and not withdrawal and not table):
                if len(values) != 1:
                    fail('A posição anterior ou seus rendimentos não estão legíveis.')
                field = 'opening' if 'SALDO ANTERIOR' in text else 'opening_provisioned_income'
                dates = re.findall(DATE, text)
                if field == 'opening':
                    if opening is not None:
                        fail('Há posições anteriores sem separação de bloco.')
                    opening = decimal(values[0])
                    opening_date = day(dates[0]) if dates else None
                else:
                    accrued = decimal(values[0])
                observations.append(observation('investment_opening_position', row,
                    amounts={field: str(decimal(values[0]))}, dates={'as_of': day(dates[0])} if dates else {}, attributes=attrs))
                continue
            label = re.sub(r'[.:(=)\-]+', ' ', text)
            summary_field = next((field for marker, field in [
                ('SALDO BRUTO', 'gross'), ('PROVISAO DE IOF', 'expected_iof'), ('PROVISAO IOF', 'expected_iof'),
                ('PROVISAO DE IRRF', 'expected_irrf'), ('PROVISAO IRRF', 'expected_irrf'),
                ('SALDO LIQUIDO', 'net'), ('LIQUIDO PARA SAQUE', 'net'),
                ('SALDO ATUAL', 'principal'), ('RENDIMENTOS PROVISIONADOS', 'provisioned_income')]
                if marker in label and values), None)
            if summary_field:
                if len(values) != 1:
                    fail('Uma posição de saque SICREDINVEST está ilegível.')
                value = decimal(values[0])
                observations.append(observation('investment_withdrawal_position', row, amounts={summary_field: str(value)},
                    dates={'as_of': as_of} if as_of else {}, attributes=attrs))
                if withdrawal:
                    if summary_field in summary:
                        fail('Há campos repetidos sem associação na posição de saque.')
                    summary[summary_field] = value
                    summary_refs.extend(row.refs)
                continue
            if re.match(r'^\d{1,2}/', text) or (values and not any(marker in text for marker in (
                    'IMPRESSO', 'EMITIDO', 'FONE', 'OUVIDORIA', 'SAC', 'CNPJ', 'CPF', 'TRIBUTACAO'))):
                fail('Há uma linha financeira SICREDINVEST não reconhecida; o relatório não pode ser extraído parcialmente.')
        if not monthly or totals is None or opening is None or accrued is None or not as_of:
            fail('Faltam posições, totais ou a data da posição de saque SICREDINVEST.')
        if not {'gross', 'net', 'expected_irrf', 'expected_iof'} <= summary.keys():
            fail('A posição de saque SICREDINVEST está incompleta.')
        previous, previous_accrued = opening, accrued
        previous_month = None
        for item in monthly:
            a = {k: Decimal(v) for k, v in item['amounts'].items()}
            m = month(item['attributes']['statement_month'])
            if previous_month is not None:
                checks.append(_check('sicredi_month_coverage', m == previous_month+1, True, refs=item['source_refs']))
            checks.append(_check('sicredi_month_principal',
                previous+a['applications']-a['redemptions']+a['paid_income']-a['withheld_irrf']-a['withheld_iof'],
                a['principal'], refs=item['source_refs']))
            checks.append(_check('sicredi_provisioned_income', previous_accrued+a['month_income']-a['paid_income'],
                                 a['provisioned_income'], refs=item['source_refs']))
            previous, previous_accrued, previous_month = a['principal'], a['provisioned_income'], m
        for field, expected in totals.items():
            checks.append(_check('sicredi_total_'+field, sum(Decimal(r['amounts'][field]) for r in monthly), expected,
                                 refs=refs(section)))
        checks.append(_check('sicredi_withdrawal_gross', previous+previous_accrued, summary['gross'], refs=summary_refs))
        checks.append(_check('sicredi_withdrawal_net', summary['gross']-summary['expected_irrf']-summary['expected_iof'],
                             summary['net'], refs=summary_refs))
        for field, expected in [('principal', previous), ('provisioned_income', previous_accrued)]:
            if field in summary:
                checks.append(_check('sicredi_position_'+field, summary[field], expected, refs=summary_refs))
        if source_account is None:
            checks.append(_check('source_account_unconfirmed', False, True, refs=refs(section)))
        else:
            continuity(checks, prior, (source_account, product), month(monthly[0]['attributes']['statement_month']),
                       previous_month, opening, previous, refs(section))
        if opening_date:
            checks.append(_check('sicredi_opening_precedes_period',
                month(opening_date[5:7]+'/'+opening_date[:4])+1 == month(monthly[0]['attributes']['statement_month']), True))
        # A later page with unrelated text cannot silently disappear.
        for page in {r.page.page_number for r in section}:
            if page not in seen_pages and not any(r.page.page_number == page and
                    any(s in r.normalized for s in ('POSICAO', 'SAQUE', 'SALDO', 'PROVISAO')) for r in section):
                fail('Uma página posterior não pertence a uma seção reconhecida do investimento.')
    return document('Sicredi', 'sicredi_term_deposit_monthly', observations, checks, {'blocks': len(sections)})
