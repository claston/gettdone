import csv
from io import BytesIO, StringIO

import pytest
from openpyxl import load_workbook

from app.application import pdf_parser
from tests.fixtures.cdb_positions import cdb_pdf, cdb_source
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case

pytestmark = pytest.mark.real_http


def _document(representation, monkeypatch):
    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    if representation == 'native':
        return cdb_pdf()
    source = cdb_source()
    raw = configure_ocr(monkeypatch, source)
    monkeypatch.setattr(pdf_parser, 'map_textract_blocks_to_extraction', lambda **kwargs: source)
    return raw


def _assert_report(analysis):
    report = analysis['financial_document']
    assert analysis['transactions_total'] == 0
    assert analysis['total_inflows'] == analysis['total_outflows'] == 0
    assert analysis['opening_balance'] is analysis['closing_balance'] is None
    assert analysis['document_type'] == 'investment_statement'
    assert report['events'] == []
    assert report['metadata']['position_count'] == 4
    assert len([row for row in report['observations'] if row['observation_type'] == 'fixed_income_position']) == 4
    assert report['export']['available_formats'] == ['csv', 'xlsx']


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_free_upload_and_downloads_preserve_positions_and_refuse_bank_ofx(tmp_path, monkeypatch, representation):
    raw = _document(representation, monkeypatch)
    with public_client(tmp_path) as client:
        owner = {'anonymous_fingerprint': 'fp-cdb-position-report'}
        response = client.post('/convert', data=owner, files={'file': ('positions.pdf', raw, 'application/pdf')})
        assert response.status_code == 200, response.text
        analysis = response.json()['analysis']
        _assert_report(analysis)
        aid = analysis['analysis_id']
        assert client.get(f'/report/{aid}', params=owner).status_code == 200
        exported = client.get(f'/convert-report/{aid}', params={**owner, 'format': 'csv'})
        assert exported.status_code == 200, exported.text
        records = list(csv.DictReader(StringIO(exported.content.decode('utf-8-sig'))))
        positions = [r for r in records if r['observation_type'] == 'fixed_income_position']
        assert len(positions) == 4
        assert all(row['dates.as_of'] == '2026-10-31' and row['attributes.unit_price'] == '1.100000' for row in positions)
        assert not any(row['record_type'] == 'event' for row in records)
        workbook = client.get(f'/convert-report/{aid}', params={**owner, 'format': 'xlsx'})
        assert workbook.status_code == 200, workbook.text
        book = load_workbook(BytesIO(workbook.content))
        assert book['Eventos'].max_row == 1 and 'Validações' in book.sheetnames
        assert 'Quantidade do ativo' in [cell.value for cell in book['Posições'][1]]
        rejected = client.get(f'/convert-report/{aid}', params={**owner, 'format': 'ofx', 'bank_code': '208',
                              'bank_branch': '0001', 'account_number': '12345'})
        assert rejected.status_code == 400 and 'OFX' in rejected.text
        supported = client.post('/convert', data=owner, files={
            'file': ('statement.csv', b'date,description,amount\n2026-10-01,PIX,50\n', 'text/csv')})
        assert supported.status_code == 200, supported.text
        assert client.get('/report/an_cdb_not_found', params=owner).status_code == 404
        assert client.post('/analyze').status_code == 405


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_worker_uses_the_same_position_contract(tmp_path, monkeypatch, representation):
    raw = _document(representation, monkeypatch)
    with worker_case(tmp_path, raw, filename='positions.pdf') as (client, job):
        _assert_report(job['result']['analysis'])
        assert client.get(f"/convert-report/{job['analysis_id']}", params={'format': 'csv'}).status_code == 200
        assert client.get(f"/convert-report/{job['analysis_id']}", params={'format': 'ofx'}).status_code == 400
