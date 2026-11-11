"""Account institutions and optional coverage never change financial meaning."""
import json

import pytest

from app.application.errors import InvalidFileContentError
from tests.test_management_ledger import ledger_page, parse


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')


def bank_page(bank, **kwargs):
    cells = [cell for cell in ledger_page(**kwargs) if not cell[0].startswith('Banco:')]
    return cells + [(f'Banco: {bank}', .70, .012)] if bank else cells


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('codes', [('001', '237'), (None, None)])
def test_different_or_unconfirmed_banks_cannot_share_one_ofx_account(representation, codes):
    pages = [bank_page(codes[0], title='Banco do Brasil' if codes[0] else 'Conta operacional'),
        bank_page(codes[1], title='Banco Bradesco' if codes[1] else 'Outra conta',
            period='01/11/2026 até 30/11/2026', opening='80,00', day='01/11', balance='60,00')]
    result = parse(pages, representation)
    doc = result.financial_document
    assert [t.amount for t in result.transactions] == [-20, -20]
    assert doc['export']['available_formats'] == ['csv', 'xlsx']
    assert len(json.loads(doc['metadata']['blocks'])) == 2
    if codes[0]:
        assert [e['attributes']['source_bank_code'] for e in doc['events']] == list(codes)
    else:
        assert result.parse_metrics['export_recommendation'] == 'review_recommended'


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_renamed_account_with_confirmed_same_bank_keeps_continuity(representation):
    result = parse([bank_page('001'), bank_page('001', title='Novo nome',
        period='01/11/2026 até 30/11/2026', opening='80,00', day='01/11', balance='60,00')], representation)
    assert 'ofx' in result.financial_document['export']['available_formats']
    assert all(c['status'] == 'passed' for c in result.financial_document['validations'])


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('period_text', [None, 'Período de ??/??/???? até ??/??/???? (Página 1/1)'])
def test_complete_dates_without_coverage_metadata_are_preserved_with_review(representation, period_text):
    cells = bank_page('001')
    cells = [(text + '/2026' if text in {'30/09', '01/10'} else text, x, y)
             for text, x, y in cells if not text.startswith('Período de')]
    if period_text:
        cells.append((period_text, .34, .10))
    result = parse([cells], representation)
    assert [t.date for t in result.transactions] == ['2026-10-01']
    assert result.transactions[0].amount == -20
    assert result.parse_metrics['export_recommendation'] == 'review_recommended'
    assert result.financial_document['export']['available_formats'] == ['csv', 'xlsx']
    block, = json.loads(result.financial_document['metadata']['blocks'])
    assert block['period_start'] is None and block['period_end'] is None


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_abbreviated_date_without_year_context_is_still_rejected(representation):
    cells = [cell for cell in bank_page('001') if not cell[0].startswith('Período de')]
    with pytest.raises(InvalidFileContentError):
        parse([cells], representation)


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_conflicting_bank_identifiers_keep_review_and_no_ofx(representation):
    result = parse([bank_page('237', title='Banco do Brasil')], representation)
    assert result.financial_document['export']['available_formats'] == ['csv', 'xlsx']
    assert result.parse_metrics['export_recommendation'] == 'review_recommended'


@pytest.mark.real_http
def test_http_distinct_banks_cannot_be_forced_into_one_ofx(tmp_path):
    from tests.fixtures.recovery_acceptance import grid_pdf
    from tests.ocr_conversion_http_helpers import public_client

    pages = [bank_page('001'), bank_page('237', period='01/11/2026 até 30/11/2026',
        opening='80,00', day='01/11', balance='60,00')]
    with public_client(tmp_path) as client:
        owner = {'anonymous_fingerprint': 'management-distinct-bank-review'}
        response = client.post('/convert', data=owner, files={'file': ('ledger.pdf', grid_pdf(pages), 'application/pdf')})
        assert response.status_code == 200, response.text
        ident = response.json()['analysis']['analysis_id']
        assert response.json()['analysis']['transactions_total'] == 2
        assert client.get('/report/' + ident, params=owner).status_code == 200
        for fmt in ('csv', 'xlsx', 'ofx'):
            exported = client.get('/convert-report/' + ident, params={**owner, 'format': fmt,
                'bank_code': '001', 'bank_branch': '1234', 'account_number': '00123-4'})
            assert exported.status_code == (400 if fmt == 'ofx' else 200), exported.text
