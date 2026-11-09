import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.period_reports import report_cells, report_source
from tests.fixtures.recovery_acceptance import grid_pdf

KINDS = ['sicredi_consolidated', 'sicredi_matrix', 'bb_funds', 'stone_payments']


def parse(kind, representation, pages=None):
    pages = report_cells(kind) if pages is None else pages
    if representation == 'native':
        return pdf_parser.parse_pdf_transactions(grid_pdf(pages))
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(report_source(pages))


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('kind', KINDS)
def test_reports_preserve_financial_meaning_and_source(kind, representation):
    result = parse(kind, representation)
    doc = result.financial_document
    assert doc is not None
    assert doc['export']['available_formats'] == ['csv', 'xlsx']
    assert all(v['status'] == 'passed' for v in doc['validations'])
    records = doc['events'] + doc['observations']
    assert records and all(r['source_refs'] for r in records)
    if kind == 'stone_payments':
        assert len(doc['events']) == 2  # legitimate repeated payments are retained
        assert all(e['cash_effect'] is None and e['amount'] == '50.00' for e in doc['events'])
        assert all(e['attributes']['card_type'] == 'Débito' for e in doc['events'])
        assert all(e['reference'] == '0000-abcd-1234' for e in doc['events'])
        assert all(t.type == 'non_cash' for t in result.transactions)
    else:
        assert not doc['events'] and not result.transactions
        if kind.startswith('sicredi'):
            monthly = [r for r in records if r['observation_type'] == 'investment_monthly_summary']
            assert [r['attributes']['statement_month'] for r in monthly] == ['09/2026', '10/2026']
            assert all(r['dates'] == {} for r in monthly)
            assert monthly[0]['amounts']['redemptions'] == '50.00'
        else:
            positions = [r for r in records if r['observation_type'] == 'investment_fund_position']
            assert len(positions) == 4
            assert {r['attributes']['fund_name'] for r in positions} == {'FUNDO TESTE A', 'FUNDO TESTE B'}
            assert {r['dates']['as_of'] for r in positions} == {'2026-09-30', '2026-10-30'}
            assert positions[-1]['attributes']['quantity'] == '10.000000'


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('kind', KINDS)
def test_unreadable_financial_cell_never_falls_back_or_returns_partial(kind, representation):
    pages = report_cells(kind)
    target = {'sicredi_consolidated': '100,00', 'sicredi_matrix': '100,00',
              'bb_funds': '110,00', 'stone_payments': 'R$ 50,00'}[kind]
    changed = False
    for index, (text, x, y) in enumerate(pages[0]):
        if text == target and not changed:
            pages[0][index] = (text.replace('0,00', '?,00'), x, y)
            changed = True
    assert changed
    with pytest.raises(PdfFinancialValidationError):
        parse(kind, representation, pages)


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('kind', KINDS)
def test_independent_repeated_blocks_preserve_pages_and_multiplicity(kind, representation):
    pages = report_cells(kind)
    one = parse(kind, representation).financial_document
    doc = parse(kind, representation, pages+pages).financial_document
    assert len(doc['events']) == 2*len(one['events'])
    assert len(doc['observations']) == 2*len(one['observations'])
    assert {ref['page'] for r in doc['events']+doc['observations'] for ref in r['source_refs']} == {1, 2}
    assert any(v['status'] == 'failed' for v in doc['validations'])  # overlap requires review, not deletion


@pytest.mark.parametrize('kind', KINDS)
def test_unreadable_later_page_is_not_ignored(kind):
    pages = report_cells(kind)+[[('10/2026 123,?? saldo ilegível', .05, .1)]]
    with pytest.raises(PdfFinancialValidationError):
        parse(kind, 'ocr', pages)


@pytest.mark.parametrize('kind', ['sicredi_consolidated', 'bb_funds'])
def test_readable_mismatch_is_preserved_for_review(kind):
    pages = report_cells(kind)
    before, after = ('1.071,40', '1.070,40') if kind.startswith('sicredi') else ('Saldo Atual = 110,00', 'Saldo Atual = 111,00')
    pages[0] = [(t.replace(before, after), x, y) for t, x, y in pages[0]]
    result = parse(kind, 'native', pages)
    assert result.financial_document['observations']
    assert any(v['status'] == 'failed' for v in result.financial_document['validations'])
    assert result.parse_metrics['export_recommendation'] == 'review_recommended'
    assert not result.transactions


@pytest.mark.parametrize('kind', KINDS)
def test_headerless_page_continuation_keeps_physical_provenance(kind):
    cells = report_cells(kind)[0]
    cut = .32 if kind.startswith('sicredi') else .65 if kind == 'bb_funds' else .28
    pages = [[(t, x, y) for t, x, y in cells if y < cut],
             [(t, x, y-cut+.05) for t, x, y in cells if y >= cut]]
    result = parse(kind, 'ocr', pages)
    doc = result.financial_document
    assert {r['page'] for item in doc['events']+doc['observations'] for r in item['source_refs']} == {1, 2}
    assert all(v['status'] == 'passed' for v in doc['validations'])


@pytest.mark.parametrize('kind', ['sicredi_consolidated', 'bb_funds'])
def test_distinct_accounts_are_kept_in_independent_blocks(kind):
    pages = report_cells(kind)
    other = [[(t.replace('12345-6', '98765-4'), x, y) for t, x, y in pages[0]]]
    doc = parse(kind, 'ocr', pages+other).financial_document
    assert {r['attributes']['source_account_number'] for r in doc['observations']} == {'12345-6', '98765-4'}
    assert all(v['status'] == 'passed' for v in doc['validations'])


def test_sicredi_gap_requires_review_without_omitting_months():
    pages = report_cells('sicredi_matrix')
    pages[0] = [(t.replace('10/2026', '11/2026').replace('31/11/2026', '30/11/2026'), x, y) for t, x, y in pages[0]]
    doc = parse('sicredi_matrix', 'ocr', pages).financial_document
    assert len([r for r in doc['observations'] if r['observation_type'] == 'investment_monthly_summary']) == 2
    assert any(v['code'] == 'sicredi_month_coverage' and v['status'] == 'failed' for v in doc['validations'])


def test_stone_unmapped_identifier_is_explicit_and_exportable(tmp_path):
    from app.application.financial_document_export import write_financial_artifacts

    pages = report_cells('stone_payments')
    pages[0] = [(t.replace('0000-abcd-', '0000\x02abcd-'), x, y) for t, x, y in pages[0]]
    doc = parse('stone_payments', 'ocr', pages).financial_document
    assert '\\u0002' in doc['events'][0]['reference']
    assert any(v['code'] == 'stone_identifier_readable' and v['status'] == 'failed' for v in doc['validations'])
    write_financial_artifacts(tmp_path, doc)
    assert (tmp_path/'converted.xlsx').is_file()


def test_bb_monthly_activity_is_an_investment_observation_not_cash():
    cells = report_cells('bb_funds')[0]
    cells += [('05/10/2026', .05, .195), ('Aplicação', .15, .195), ('10,00', .35, .195),
              ('1,000000', .72, .195), ('10,000000000', .83, .195), ('11,000000', .92, .195)]
    doc = parse('bb_funds', 'ocr', [cells]).financial_document
    moves = [o for o in doc['observations'] if o['observation_type'] == 'investment_fund_movement']
    assert len(moves) == 1 and moves[0]['amounts']['value'] == '10.00'
    assert moves[0]['dates']['as_of'] == '2026-10-05'
    assert not doc['events']
    assert any(v['code'] == 'bb_printed_applications' and v['status'] == 'failed' for v in doc['validations'])


def test_bb_without_geometry_never_converts_a_position_to_cash():
    pages = ['\n'.join(t for t, _, _ in cells) for cells in report_cells('bb_funds')]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_pdf_transactions_from_page_texts(pages)


@pytest.mark.parametrize('kind', KINDS)
def test_two_complete_blocks_on_one_page_remain_separate(kind):
    cells = report_cells(kind)[0]
    combined = [(t, x, y*.45) for t, x, y in cells]+[(t, x, .5+y*.45) for t, x, y in cells]
    one = parse(kind, 'ocr').financial_document
    doc = parse(kind, 'ocr', [combined]).financial_document
    assert len(doc['events'])+len(doc['observations']) == 2*(len(one['events'])+len(one['observations']))
    assert {r['attributes']['block'] for r in doc['events']+doc['observations']} == {1, 2}


def test_missing_geometry_does_not_silently_drop_a_financial_row():
    from dataclasses import replace

    source = report_source(report_cells('stone_payments'))
    page = source.pages[0]
    damaged = replace(page, lines=[*page.lines, replace(page.lines[0], id='missing', text='01/10/2026 R$ 99,00', word_ids=())])
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[damaged]))


@pytest.mark.parametrize('kind', ['bb_funds', 'stone_payments'])
def test_missing_coverage_header_keeps_explicit_dates_with_review(kind):
    pages = [[(t, x, y) for t, x, y in report_cells(kind)[0] if 'referência' not in t and 'Período:' not in t]]
    doc = parse(kind, 'ocr', pages).financial_document
    assert doc['events'] or doc['observations']
    assert any(v['code'].endswith('_unconfirmed') and v['status'] == 'failed' for v in doc['validations'])
    if kind == 'bb_funds':
        assert all(o['attributes']['statement_month'] is None for o in doc['observations'])
