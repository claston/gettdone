from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.application import pdf_parser
from tests.fixtures.period_reports import report_cells, report_pdf, report_source
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_period_financial_reports import KINDS

pytestmark = pytest.mark.real_http


def _raw(kind, representation, monkeypatch):
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')
    if representation == 'native':
        return report_pdf(kind)
    source = report_source(report_cells(kind))
    raw = configure_ocr(monkeypatch, source)
    monkeypatch.setattr(pdf_parser, 'map_textract_blocks_to_extraction', lambda **kwargs: source)
    return raw


def _check(analysis, kind):
    doc = analysis['financial_document']
    assert doc['export']['available_formats'] == ['csv', 'xlsx']
    assert analysis['opening_balance'] is analysis['closing_balance'] is None
    assert analysis['total_inflows'] == analysis['total_outflows'] == 0
    if kind != 'stone_payments':
        assert doc['events'] == [] and analysis['transactions_total'] == 0
    else:
        assert len(doc['events']) == 2 and all(e['cash_effect'] is None for e in doc['events'])


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_public_upload_report_exports_and_negative_paths(tmp_path, monkeypatch, kind, representation):
    raw = _raw(kind, representation, monkeypatch)
    with public_client(tmp_path) as client:
        owner = {'anonymous_fingerprint': 'fp-monthly-report-test'}
        response = client.post('/convert', data=owner, files={'file': ('report.pdf', raw, 'application/pdf')})
        assert response.status_code == 200, response.text
        analysis = response.json()['analysis']
        _check(analysis, kind)
        aid = analysis['analysis_id']
        assert client.get(f'/report/{aid}', params=owner).status_code == 200
        csv = client.get(f'/convert-report/{aid}', params={**owner, 'format': 'csv'})
        assert csv.status_code == 200 and 'source_refs' in csv.content.decode('utf-8-sig')
        response = client.get(f'/convert-report/{aid}', params={**owner, 'format': 'xlsx'})
        assert response.status_code == 200
        book = load_workbook(BytesIO(response.content))
        assert 'Validações' in book.sheetnames
        assert book['Eventos' if kind == 'stone_payments' else 'Posições'].max_row > 1
        assert client.get(f'/convert-report/{aid}', params={**owner, 'format': 'ofx',
            'bank_code': '001', 'bank_branch': '1234', 'account_number': '12345'}).status_code == 400
        assert client.get('/report/an_period_report_missing', params=owner).status_code == 404
        response = client.post('/convert', data=owner,
            files={'file': ('supported.csv', b'date,description,amount\n2026-10-01,PIX,50\n', 'text/csv')})
        assert response.status_code == 200, response.text
        assert client.post('/analyze').status_code == 405  # retired route; /convert is the supported endpoint


@pytest.mark.parametrize('kind', KINDS)
@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_worker_preserves_the_same_report_contract(tmp_path, monkeypatch, kind, representation):
    raw = _raw(kind, representation, monkeypatch)
    with worker_case(tmp_path, raw, filename='report.pdf') as (client, job):
        _check(job['result']['analysis'], kind)
        assert client.get(f"/convert-report/{job['analysis_id']}", params={'format': 'xlsx'}).status_code == 200
        assert client.get(f"/convert-report/{job['analysis_id']}", params={'format': 'ofx'}).status_code == 400
