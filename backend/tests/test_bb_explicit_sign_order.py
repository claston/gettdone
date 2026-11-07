from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_postdeploy_ocr_columns import bb_signed


@pytest.mark.parametrize("credit,debit", [
    ("(+) 50,00", "(-) 20,00"), ("50,00 (+)", "20,00 (-)"), ("(+)50,00", "20,00(-)"),
])
def test_explicit_prefix_and_suffix_signs_preserve_financial_meaning(credit, debit):
    source = bb_signed()
    page = source.pages[0]
    lines = [replace(line, text=credit if line.text == "50,00 (+)" else debit if line.text == "20,00 (-)" else line.text)
             for line in page.lines]
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert [(row.date, row.amount) for row in result.transactions] == [("2026-07-01", 50), ("2026-07-02", -20)]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)


@pytest.mark.parametrize("token", ["(+) 50,00 (-)", "(+) 5O,00", "50,00", "50,00 (+) 20,00 (-)"])
def test_unreadable_unsigned_or_conflicting_cell_cannot_be_skipped_or_reassigned(token):
    source = bb_signed()
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[
        replace(line, text=token) if line.text == "50,00 (+)" else line for line in page.lines
    ])])
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


@pytest.mark.real_http
def test_prefix_signs_work_through_free_upload_and_worker_exports(tmp_path, monkeypatch):
    source = bb_signed()
    page = source.pages[0]
    lines = [replace(line, text='(+) 50,00' if line.text == '50,00 (+)' else
                     '(-) 20,00' if line.text == '20,00 (-)' else line.text) for line in page.lines]
    raw = configure_ocr(monkeypatch, replace(source, pages=[replace(page, lines=lines)]))
    with public_client(tmp_path / 'public') as client:
        owner = {'anonymous_fingerprint': 'fp-bb-prefix-sign'}
        response = client.post('/convert', data=owner, files={'file': ('statement.pdf', raw, 'application/pdf')})
        assert response.status_code == 200, response.text
        analysis = response.json()['analysis']
        assert [row['amount'] for row in analysis['preview_transactions']] == [50, -20]
        assert analysis['opening_balance'] == 100 and analysis['closing_balance'] == 130
        assert client.get(f"/report/{analysis['analysis_id']}", params=owner).status_code == 200
        for fmt in ('csv', 'xlsx', 'ofx'):
            exported = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, 'format': fmt})
            assert exported.status_code == 200, exported.text
            if fmt == 'ofx':
                assert exported.text.count('<STMTTRN>') == 2
    with worker_case(tmp_path / 'worker', raw) as (client, job):
        assert job['result']['analysis']['transactions_total'] == 2
        exported = client.get(f"/convert-report/{job['analysis_id']}", params={'format': 'ofx'})
        assert exported.status_code == 200 and exported.text.count('<STMTTRN>') == 2
