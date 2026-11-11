"""The public HTTP path stores typed events and exports the original columns."""
import csv
from io import BytesIO, StringIO

import pytest
from openpyxl import load_workbook

from tests.fixtures.recovery_acceptance import grid_pdf
from tests.ocr_conversion_http_helpers import public_client
from tests.test_management_ledger import ledger_page

pytestmark = pytest.mark.real_http


def test_management_reports_round_trip_and_export_policy(tmp_path, monkeypatch):
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')
    with public_client(tmp_path) as client:
        for title, kind, ofx in [('Conta operacional', 'Conta Corrente', True),
                                 ('CARTÃO BRADESCO', 'Conta Corrente', False),
                                 ('Adquirente fictícia', 'Administradora de Cartões', False)]:
            owner = {'anonymous_fingerprint': 'management-ledger-http-' + title}
            response = client.post('/convert', data=owner, files={
                'file': ('ledger.pdf', grid_pdf([ledger_page(title=title, kind=kind)]), 'application/pdf')})
            assert response.status_code == 200, response.text
            analysis = response.json()['analysis']
            ident = analysis['analysis_id']
            assert analysis['transactions_total'] == 1
            report = client.get('/report/' + ident, params=owner)
            assert report.status_code == 200, report.text
            exported = client.get('/convert-report/' + ident, params={**owner, 'format': 'csv'})
            assert exported.status_code == 200, exported.text
            rows = list(csv.DictReader(StringIO(exported.content.decode('utf-8-sig'))))
            event = next(row for row in rows if row['record_type'] == 'event')
            assert event['reference'] == '000123' and event['amount'] == '-20.00'
            assert event['attributes.category'] == '3.120 - Copa e Cozinha'
            assert event['source.Situação'] == 'Conciliado'
            assert event['source.Cliente ou Fornecedor'] == 'Fornecedor de teste'
            exported = client.get('/convert-report/' + ident, params={**owner, 'format': 'xlsx'})
            assert exported.status_code == 200, exported.text
            workbook = load_workbook(BytesIO(exported.content))
            originals = list(workbook['Campos originais'].values)
            assert dict(zip(originals[0], originals[1], strict=True))['Documento'] == '000123'
            assert workbook['Validações'].max_row > 1
            exported = client.get('/convert-report/' + ident, params={**owner, 'format': 'ofx'})
            assert exported.status_code == 400
            exported = client.get('/convert-report/' + ident, params={**owner, 'format': 'ofx',
                'bank_code': '341', 'bank_branch': '1234', 'account_number': '00123-4'})
            assert exported.status_code == (200 if ofx else 400), exported.text
            if ofx:
                assert '<TRNAMT>-20.00' in exported.text
                # No global balance is invented by the receipts adapter.
                assert '<LEDGERBAL>' not in exported.text
        owner = {'anonymous_fingerprint': 'management-ledger-http-csv'}
        valid = client.post('/convert', data=owner, files={'file': ('valid.csv',
            b'date,description,amount\n2026-10-01,Entrada,100\n', 'text/csv')})
        assert valid.status_code == 200, valid.text
        assert client.get('/report/' + valid.json()['analysis']['analysis_id'], params=owner).status_code == 200
        assert client.get('/report/an_missing_management', params=owner).status_code == 404
        assert client.post('/analyze').status_code == 405
        invalid = client.post('/convert', data=owner, files={'file': ('invalid.pdf',
            grid_pdf([ledger_page(amount='-?0,00')]), 'application/pdf')})
        assert invalid.status_code == 400, invalid.text
