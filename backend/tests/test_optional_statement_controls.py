"""Fictional regressions: optional controls never invent financial context."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_pdf_parser_digital_credit_card import native_pdf
from tests.test_recovery_sweep_ocr import extraction
from tests.test_sicoob_statement_context import modern_cells, parse_cells


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')


def no_opening(*, descending=False, mismatch=False):
    cells = modern_cells(descending=descending, mismatch=mismatch)
    y = next(y for text, _, y in cells if text == 'SALDO ANTERIOR')
    return [cell for cell in cells if cell[2] != y]


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('descending,mismatch', [(False, False), (True, False), (False, True), (True, True)])
def test_missing_opening_keeps_movements_and_checks_only_available_intervals(representation, descending, mismatch):
    result = parse_cells(no_opening(descending=descending, mismatch=mismatch), representation)
    assert [t.amount for t in result.transactions] == [50, -10, -10]
    assert result.parse_metrics['balance_bounds_unavailable'] == 1
    assert 'verified_opening_balance' not in result.parse_metrics
    assert 'derived_opening_balance' not in result.parse_metrics
    assert result.parse_metrics['financial_controls_validated'] == 0
    assert result.parse_metrics['export_recommendation'] == 'review_recommended'
    assert result.parse_metrics.get('statement_validation_failed', 0) == int(mismatch)
    assert [r.running_balance for r in result.canonical_transactions] == [150, None, 130 + int(mismatch)]
    assert all(r.source_regions and r.source_line_refs for r in result.canonical_transactions)


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('bad', ['R$ 10,00', 'R$ 1?,00D', 'R$ 10,00?'])
def test_missing_opening_does_not_allow_missing_or_ambiguous_amount_sign(representation, bad):
    cells = [(bad if text == 'R$ 10,00D' else text, x, y) for text, x, y in no_opening()]
    with pytest.raises(InvalidFileContentError):
        parse_cells(cells, representation)


def ascending_cards(*, mismatch=False):
    return [('Extrato por Período', .05, .03), ('Ordenar', .8, .06),
        ('Saldo Anterior', .08, .10), ('R$ 0,00', .72, .10),
        ('03 de Agosto de 2026, Segunda-feira', .04, .16),
        ('Crédito teste', .20, .21), ('R$ 50,00', .72, .21), ('03AGO', .83, .24),
        ('Pagamento teste', .20, .29), ('-R$ 50,00', .72, .29), ('03AGO', .83, .32),
        ('Saldo do dia', .08, .37), ('R$ 0,00', .72, .37),
        ('11 de Agosto de 2026, Terça-feira', .04, .43),
        ('Crédito teste', .20, .48), ('R$ 20,00', .72, .48), ('11AGO', .83, .51),
        ('Saldo do dia', .08, .56), ('R$ 21,00' if mismatch else 'R$ 20,00', .72, .56),
        ('25 de Agosto de 2026, Terça-feira', .04, .62),
        ('Tarifa teste', .20, .67), ('-R$ 5,00', .72, .67), ('25AGO', .83, .70),
        ('Saldo do dia', .08, .75), ('R$ 15,00', .72, .75),
        ('Compartilhar', .30, .83), ('Voltar', .30, .90)]


def parse_cards(cells, representation):
    if representation == 'native':
        return pdf_parser.parse_pdf_transactions(grid_pdf([cells]))
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction(cells))


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('mismatch', [False, True])
def test_ascending_cards_keep_final_day_and_all_printed_controls(representation, mismatch):
    result = parse_cards(ascending_cards(mismatch=mismatch), representation)
    assert [t.amount for t in result.transactions] == [50, -50, 20, -5]
    assert [t.date for t in result.transactions] == ['2026-08-03'] * 2 + ['2026-08-11', '2026-08-25']
    assert result.parse_metrics.get('statement_validation_failed', 0) == int(mismatch)
    assert result.parse_metrics['printed_balance_checks'] == 3
    assert result.parse_metrics['verified_opening_balance'] == 0
    assert result.parse_metrics['verified_closing_balance'] == 15
    assert all(r.source_regions for r in result.canonical_transactions)


@pytest.mark.parametrize('mutation', ['bad_tag', 'orphan', 'mixed_days', 'bad_amount'])
def test_ascending_cards_do_not_hide_unreadable_or_unassigned_rows(mutation):
    cells = ascending_cards()
    changes = {'bad_tag': {'25AGO': '24AGO'}, 'mixed_days': {'25 de Agosto de 2026, Terça-feira':
        '05 de Agosto de 2026, Quarta-feira', '25AGO': '05AGO'}, 'bad_amount': {'-R$ 5,00': '-R$ ?,00'}}
    cells = [(changes.get(mutation, {}).get(text, text), x, y) for text, x, y in cells]
    if mutation == 'orphan':
        cells.append(('R$ 99,00', .72, .80))
    with pytest.raises(InvalidFileContentError):
        parse_cards(cells, 'native')


BB = '''Extrato de Conta Corrente
Movimentação em Agosto de 2026
Data Origem Histórico Documento Lote Valor R$
03/08/2026 1234-5 Pix - Recebido 12345 14397 100,00 C
03/08/2026 1234-5 Saldo do dia 500,00 C
Conta corrente + Rende Fácil
04/08/2026 1234-5 Pagamento de Boleto 12346 13105 30,00 D
COMPLEMENTO FICTÍCIO
04/08/2026 1234-5 Saldo do dia 470,00 C
Conta corrente + Rende Fácil
bb.com.br - Central de Atendimento BB'''


def test_bb_control_without_reference_preserves_cash_and_combined_balance_meaning():
    result = pdf_parser.parse_pdf_transactions(native_pdf([BB]))
    assert [t.amount for t in result.transactions] == [100, -30]
    assert 'COMPLEMENTO FICTÍCIO' in result.transactions[1].description
    assert [r.external_reference_id for r in result.canonical_transactions] == ['12345', '12346']
    assert all(r.running_balance is None for r in result.canonical_transactions)
    assert result.parse_metrics['balance_validation_status'] == 'combined_cash_investment_not_comparable'
    assert result.parse_metrics['balance_bounds_unavailable'] == 1
    assert result.parse_metrics['export_recommendation'] == 'review_recommended'


@pytest.mark.parametrize('before,after', [('500,00 C', '500,00 ?'), ('500,00 C', '5?,00 C'),
    ('12346 13105 30,00 D', '30,00 D'), ('30,00 D', '30,00')])
def test_bb_optional_control_reference_does_not_relax_movement_fields(before, after):
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(native_pdf([BB.replace(before, after)]))


def test_bb_ocr_already_supports_control_without_reference_or_lot():
    from tests.test_final_recovery_ocr import bb_combined

    source = bb_combined()
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[r for r in page.lines
        if not (r.text == '13105' and r.bbox['top'] == .2)])])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [-50]
    assert result.parse_metrics['financial_controls_validated'] == 0


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_sicoob_missing_opening_preserves_reference_and_cross_page_history(representation):
    first = no_opening()
    first = [c for c in first if c[2] < .46]
    first += [('1001', .12, .325), ('1002', .12, .435), ('Página 1/2', .05, .94)]
    second = [('Data', .05, .08), ('Documento', .12, .08), ('Histórico', .24, .08), ('Valor', .9, .08),
        ('COMPLEMENTO FICTÍCIO DA PÁGINA ANTERIOR', .24, .13),
        ('02/07', .05, .20), ('1003', .12, .20), ('PAGAMENTO TESTE', .24, .20), ('R$ 10,00D', .86, .20),
        ('02/07', .05, .26), ('SALDO DO DIA', .24, .26), ('R$ 130,00C', .86, .26),
        ('RESUMO', .24, .32), ('Página 2/2', .05, .94)]
    if representation == 'native':
        result = pdf_parser.parse_pdf_transactions(grid_pdf([first, second]))
    else:
        source = extraction(first)
        result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(
            replace(source, pages=[source.pages[0], extraction(second, page=2).pages[0]]))
    assert [t.amount for t in result.transactions] == [50, -10, -10]
    assert [r.external_reference_id for r in result.canonical_transactions] == ['1001', '1002', '1003']
    assert 'COMPLEMENTO FICTÍCIO DA PÁGINA ANTERIOR' in result.transactions[1].description
    assert {page for page, _ in result.canonical_transactions[1].source_line_refs} == {1, 2}


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_multiple_months_without_openings_preserve_pages_and_require_review(representation):
    first = no_opening()
    second = [(text.replace('/07', '/08'), x, y) for text, x, y in first]
    if representation == 'native':
        result = pdf_parser.parse_pdf_transactions(grid_pdf([first, second]))
    else:
        source = extraction(first)
        result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(
            replace(source, pages=[source.pages[0], extraction(second, page=2).pages[0]]))
    assert [t.amount for t in result.transactions] == [50, -10, -10] * 2
    assert {r.source_page for r in result.canonical_transactions} == {1, 2}
    assert result.parse_metrics['export_recommendation'] == 'review_recommended'
    assert result.parse_metrics['financial_controls_validated'] == 0
    assert result.parse_metrics['balance_bounds_unavailable'] == 1


@pytest.mark.real_http
def test_optional_controls_http_preserves_movements_and_export_balance_limits(tmp_path):
    from tests.ocr_conversion_http_helpers import public_client

    cases = [('cards.pdf', grid_pdf([ascending_cards()]), [50, -50, 20, -5]),
        ('sicoob.pdf', grid_pdf([no_opening(mismatch=True)]), [50, -10, -10]),
        ('bb.pdf', native_pdf([BB]), [100, -30]),
        ('cash.csv', b'date,description,amount\n2026-08-03,Entrada,10\n', [10])]
    with public_client(tmp_path) as client:
        for name, raw, expected in cases:
            owner = {'anonymous_fingerprint': 'synthetic-optional-controls-' + name}
            response = client.post('/convert', data=owner, files={'file': (name, raw,
                'text/csv' if name.endswith('.csv') else 'application/pdf')})
            assert response.status_code == 200, response.text
            data = response.json()['analysis']
            assert [r['amount'] for r in data['preview_transactions']] == expected
            if name in {'sicoob.pdf', 'bb.pdf'}:
                assert data['opening_balance'] is None
                assert data['pdf_processing_metrics']['financial_controls_validated'] == 0
                assert data['review_guidance']['category'] != 'information'
            ident = data['analysis_id']
            assert client.get('/report/' + ident, params=owner).status_code == 200
            for fmt in ('csv', 'xlsx', 'ofx'):
                exported = client.get('/convert-report/' + ident, params={**owner, 'format': fmt})
                assert exported.status_code == 200, exported.text
                if fmt == 'ofx':
                    assert exported.text.count('<STMTTRN>') == len(expected)
                    if name in {'sicoob.pdf', 'bb.pdf'}:
                        assert '<LEDGERBAL>' not in exported.text
        assert client.get('/report/an_missing_optional_controls', params=owner).status_code == 404
        assert client.post('/analyze').status_code == 405
