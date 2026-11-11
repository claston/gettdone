"""Unreadable later ledger rows cannot be reported as complete extraction."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_optional_statement_controls import no_opening
from tests.test_recovery_sweep_ocr import extraction


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('date', ['??/07', 'xx/07', ''])
@pytest.mark.parametrize('with_readable_row', [False, True])
@pytest.mark.parametrize('summary', [False, True])
def test_orphan_later_page_amount_never_disappears(representation, date, with_readable_row, summary, monkeypatch):
    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    first = no_opening() if summary else [cell for cell in no_opening() if cell[2] < .65]
    second = [('Data', .05, .08), ('Documento', .12, .08), ('Histórico', .24, .08), ('Valor', .9, .08),
        ('1003', .12, .20), ('PAGAMENTO NA SEGUNDA PÁGINA', .24, .20), ('R$ 25,00D', .86, .20)]
    if date:
        second.append((date, .05, .20))
    if with_readable_row:
        second += [('03/07', .05, .40), ('1004', .12, .40), ('PAGAMENTO LEGÍVEL', .24, .40), ('R$ 10,00D', .86, .40)]
    with pytest.raises(InvalidFileContentError):
        if representation == 'native':
            pdf_parser.parse_pdf_transactions(grid_pdf([first, second]))
        else:
            source = extraction(first)
            pdf_parser._parse_scanned_pdf_with_textract_text_pages(
                replace(source, pages=[source.pages[0], extraction(second, page=2).pages[0]]))


@pytest.mark.real_http
def test_http_unreadable_later_page_is_not_a_successful_partial_report(tmp_path, monkeypatch):
    from tests.ocr_conversion_http_helpers import public_client

    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    first = [cell for cell in no_opening() if cell[2] < .65]
    second = [('Data', .05, .08), ('Documento', .12, .08), ('Histórico', .24, .08), ('Valor', .9, .08),
        ('??/07', .05, .20), ('PAGAMENTO ILEGÍVEL', .24, .20), ('R$ 25,00D', .86, .20)]
    with public_client(tmp_path) as client:
        response = client.post('/convert', data={'anonymous_fingerprint': 'sicoob-coverage-review'},
            files={'file': ('ledger.pdf', grid_pdf([first, second]), 'application/pdf')})
        assert response.status_code == 400, response.text
        assert 'analysis' not in response.json()
