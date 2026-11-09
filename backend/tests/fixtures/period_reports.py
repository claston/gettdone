"""Fictional report cells, shared by native PDF and OCR regression cases."""
from dataclasses import replace

from app.application.document_extraction_models import RawDocumentExtraction
from tests.fixtures.cdb_positions import cdb_source
from tests.fixtures.recovery_acceptance import grid_pdf


def report_cells(kind):
    if kind.startswith('sicredi'):
        matrix = kind == 'sicredi_matrix'
        title = ('EXTRATO DE DEPOSITO A PRAZO DETALHADO - POR PRODUTO' if matrix else
                 'Extrato de Aplicação - Depósito a Prazo - Detalhado - Consolidado')
        cells = [('09/11/2026 SISTEMA SICREDI - APLICACOES AUTOMATICAS - 7.70 PAGINA: 0001'
                  if matrix else 'SICREDI', .05, .03), (title, .05, .06),
                 ('Cooperativa: 1234 Conta Corrente: 12345-6', .05, .09),
                 ('Produto: SICREDINVEST AUTOMATICO' if matrix else 'Produto: SICREDINVEST', .05, .12),
                 ('Período de Consulta: 09/2026 a 10/2026', .05, .15),
                 ('Saldo Anterior: 1.000,00 31/08/2026', .05, .18),
                 ('Rendimentos Provisionados: 10,00 31/08/2026', .05, .21)]
        xs = [.05, .17, .27, .37, .48, .57, .66, .76, .86]
        labels = ['Mês/Ano', 'Aplicações', 'Resgates', 'Rendimentos Pagos', 'IRRF', 'IOF', 'No Mês', 'Acumulado', 'Saldo Atual']
        cells += [(label, x, .26) for label, x in zip(labels, xs)]
        for y, values in [(.30, ['09/2026', '100,00', '50,00', '4,00', '1,00', '0,00', '8,00', '14,00', '1.053,00']),
                          (.34, ['10/2026', '0,00', '0,00', '0,00', '0,00', '0,00', '9,00', '23,00', '1.053,00'])]:
            cells += [(value, x, y) for value, x in zip(values, xs)]
        cells += [('TOTAIS', .05, .38)] + [(v, x, .38) for v, x in zip(['100,00', '50,00', '4,00', '1,00', '0,00'], xs[1:6])]
        cells += [('POSIÇÃO PARA SAQUE EM 31/10/2026', .05, .43), ('Saldo Atual 1.053,00', .05, .46),
                  ('Rendimentos Provisionados 23,00', .05, .49), ('Saldo Bruto 1.076,00', .05, .52),
                  ('Provisão IRRF 4,60', .05, .55), ('Provisão IOF 0,00', .05, .58), ('Líquido para Saque 1.071,40', .05, .61)]
        return [cells]
    if kind == 'bb_funds':
        cells = [('Extratos - Investimentos Fundos - Mensal', .05, .03), ('Banco do Brasil', .65, .03),
                 ('09/11/2026 16:13:02', .60, .06),
                 ('Agência 1234-5 Conta 12345-6', .05, .06), ('Mês/ano referência OUTUBRO/2026', .05, .09)]
        for offset, fund in [(0, 'FUNDO TESTE A'), (.44, 'FUNDO TESTE B')]:
            cells += [(fund+' - CNPJ: 00.000.000/0001-00', .05, .12+offset)]
            headers = [('Data', .05), ('Histórico', .15), ('Valor', .35), ('Valor IR', .44), ('Prej. Comp.', .53),
                       ('Valor IOF', .63), ('Quantidade cotas', .72), ('Valor cota', .83), ('Saldo cotas', .92)]
            cells += [(label, x, .15+offset) for label, x in headers]
            for y, date, label, value in [(.18, '30/09/2026', 'Saldo anterior', '100,00'),
                                         (.21, '30/10/2026', 'Saldo Atual', '110,00')]:
                cells += [(date, .05, y+offset), (label, .15, y+offset), (value, .35, y+offset),
                          ('10,000000', .72, y+offset)]
                if label == 'Saldo Atual':
                    cells += [('10,000000', .92, y+offset)]
            cells += [('Resumo do mês', .05, .24+offset)]
            for i, (label, value) in enumerate([('Saldo anterior', '100,00'), ('Aplicações (+)', '0,00'),
                    ('Resgates (-)', '0,00'), ('Rendimento Bruto (+)', '10,00'), ('Imposto de Renda (-)', '0,00'),
                    ('IOF (-)', '0,00'), ('Rendimento Líquido', '10,00'), ('Saldo Atual =', '110,00')]):
                cells += [(label+' '+value, .05, .26+offset+i*.016)]
        return [cells]
    return [[('Comprovante de pagamento', .04, .03), ('Stone Pagamentos S.A.', .04, .06),
             ('Agência 0001 Conta 12345-6', .04, .09), ('Período: de 01/10/2026 a 01/10/2026', .04, .12),
             *[(v, x, .18) for v, x in zip(['DATA', 'VALOR', 'MODALIDADE', 'BANDEIRA', 'SITUAÇÃO', 'IDENTIFICAÇÃO', 'CÓDIGO'],
                                          [.04, .17, .30, .43, .55, .68, .86])],
             *[(v, x, y) for y in [.23, .30] for v, x in zip(
                 ['01/10/2026', 'R$ 50,00', 'Débito', 'VISA', 'Pago', '0000-abcd-', '-'],
                 [.04, .17, .30, .43, .55, .68, .86])],
             ('1234', .68, .245), ('1234', .68, .315),
             ('Entenda a situação dos pagamentos', .04, .40), ('Pago', .04, .43),
             ('Pagamento enviado e recebido pela instituição da conta de recebimento.', .04, .46)]]


def report_source(pages):
    result = []
    for number, cells in enumerate(pages, 1):
        page = cdb_source(cells).pages[0]
        result.append(replace(page, page_number=number,
            lines=[replace(line, page_number=number) for line in page.lines],
            words=[replace(word, page_number=number) for word in page.words]))
    return RawDocumentExtraction('aws_textract', 'synthetic-period-reports', result, {})


def report_pdf(kind):
    return grid_pdf(report_cells(kind))
