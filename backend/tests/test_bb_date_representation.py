"""Movement dates must not change when PDF/OCR splits the date columns."""
import pytest

from tests.test_nov10_priority_recovery import bb_paired_dates_cells
from tests.test_sicoob_channel_pending import parse_pages


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_joined_and_separate_bb_dates_keep_the_movement_date(representation, monkeypatch):
    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    joined = bb_paired_dates_cells()
    separated = [(text, x, y) for text, x, y in joined if text != '01/10/2026 30/09/2026']
    separated += [('01/10/2026', .08, .22), ('30/09/2026', .22, .22)]
    results = [parse_pages([cells], representation) for cells in [joined, separated]]
    assert [[t.date for t in r.transactions] for r in results] == [['2026-09-30', '2026-10-01']] * 2
    assert [[t.amount for t in r.transactions] for r in results] == [[-20, 20]] * 2
    assert all(r.canonical_transactions[0].source_regions for r in results)


@pytest.mark.real_http
def test_http_separate_bb_dates_export_movement_date(tmp_path, monkeypatch):
    from tests.fixtures.recovery_acceptance import grid_pdf
    from tests.ocr_conversion_http_helpers import public_client

    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    cells = [cell for cell in bb_paired_dates_cells() if cell[0] != '01/10/2026 30/09/2026']
    cells += [('01/10/2026', .08, .22), ('30/09/2026', .22, .22)]
    with public_client(tmp_path) as client:
        owner = {'anonymous_fingerprint': 'bb-date-role-review'}
        response = client.post('/convert', data=owner, files={'file': ('bb.pdf', grid_pdf([cells]), 'application/pdf')})
        assert response.status_code == 200, response.text
        analysis = response.json()['analysis']
        assert [r['date'] for r in analysis['preview_transactions']] == ['2026-09-30', '2026-10-01']
        exported = client.get('/convert-report/' + analysis['analysis_id'], params={**owner, 'format': 'ofx'})
        assert exported.status_code == 200, exported.text
        assert exported.text.count('<DTPOSTED>20261001000000') == 1
        assert exported.text.count('<DTPOSTED>20260930000000') == 1
