from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedLine, ExtractedPage
from app.application.errors import InvalidFileContentError
from app.application.financial_document_detection import detect_financial_report
from tests.fixtures.cdb_positions import ASSETS, cdb_cells, cdb_pdf, cdb_source


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_positions_and_details_are_four_observations_not_cash_movements(representation, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    result = (pdf_parser.parse_pdf_transactions(cdb_pdf()) if representation == "native"
              else pdf_parser._parse_scanned_pdf_with_textract_text_pages(cdb_source()))
    report = result.financial_document
    assert report and report["document_type"] == "investment_statement"
    assert result.transactions == [] and report["events"] == []
    positions = [o for o in report["observations"] if o["observation_type"] == "fixed_income_position"]
    assert len(positions) == 4
    assert {o["attributes"]["asset_code"] for o in positions} == set(ASSETS)
    assert all(o["dates"]["as_of"] == "2026-10-31" for o in positions)
    assert all(o["dates"]["application"] == "2026-09-02" for o in positions)
    assert all(o["amounts"]["gross"] == "110.00" and o["amounts"]["net"] == "108.00" for o in positions)
    assert all(o["attributes"]["unit_price"] == "1.100000" for o in positions)
    assert all(len(o["source_refs"]) > 1 for o in positions)
    assert all(v["status"] == "passed" for v in report["validations"])
    assert report["export"]["available_formats"] == ["csv", "xlsx"]


@pytest.mark.parametrize("token", ["10?,00", "110,00 (-)"])
def test_unreadable_money_does_not_fall_back_to_generic_bank_parser(token):
    source = cdb_source()
    page = source.pages[0]
    lines = [replace(line, text=token) if line.text == "110,00" and line.bbox["top"] < .5 else line for line in page.lines]
    # Keep word evidence consistent with the changed cell.
    changed_ids = {w for line in lines if line.text == token for w in line.word_ids}
    words = [replace(w, text=token) if w.id in changed_ids else w for w in page.words]
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines, words=words)]))


def test_money_mismatch_preserves_printed_fields_and_requires_review():
    source = cdb_source()
    page = source.pages[0]
    lines = [replace(line, text="107,00") if line.text == "108,00" else line for line in page.lines]
    changed_ids = {w for line in lines if line.text == "107,00" for w in line.word_ids}
    words = [replace(w, text="107,00") if w.id in changed_ids else w for w in page.words]
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines, words=words)]))
    assert result.transactions == []
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert any(v["status"] == "failed" for v in result.financial_document["validations"])


def _remove_lines(source, predicate):
    pages = []
    for page in source.pages:
        removed = {word for line in page.lines if predicate(line) for word in line.word_ids}
        pages.append(replace(page, lines=[line for line in page.lines if not predicate(line)],
                             words=[word for word in page.words if word.id not in removed]))
    return replace(source, pages=pages)


def test_missing_tax_is_explicit_review_and_never_inferred_as_zero():
    source = _remove_lines(cdb_source(), lambda line: line.text == '-' and .89 < line.bbox['left'] < .93)
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    positions = [o for o in result.financial_document['observations'] if o['observation_type'] == 'fixed_income_position']
    assert len(positions) == 4 and result.transactions == []
    assert all('iof' not in o['amounts'] for o in positions)
    assert result.parse_metrics['export_recommendation'] == 'review_recommended'


@pytest.mark.parametrize('later_page', [ExtractedPage(2), *[ExtractedPage(2, lines=[
    ExtractedLine('unreadable', 2, 1, title, bbox={'left': .1, 'top': .1, 'width': .6, 'height': .01})])
    for title in ('Renda variável - Posição - Ações', 'Renda fixa - Posição - LCI')]])
def test_unreadable_or_uncovered_later_section_cannot_be_silently_omitted(later_page):
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(cdb_source(), pages=[cdb_source().pages[0], later_page]))


def test_position_date_must_come_from_the_printed_period():
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(_remove_lines(cdb_source(), lambda line: 'Período de' in line.text))


def test_disclaimer_definitions_do_not_count_as_unsupported_position_tables():
    source = cdb_source()
    disclaimer = ExtractedPage(2, lines=[ExtractedLine('disclaimer', 2, 1, 'Disclaimers'),
        ExtractedLine('definition', 2, 2, 'Renda fixa - Posição')])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[*source.pages, disclaimer]))
    assert result.financial_document['metadata']['position_count'] == 4 and result.transactions == []


def test_counterparty_and_investment_appendix_do_not_reclassify_a_bank_statement():
    text = 'Extrato de Conta Corrente\nBanco Exemplo\nPIX BTG INVESTIMENTO\n' + cdb_source().pages[0].lines[0].text
    text += '\nCDB QUANTIDADE BRUTO IR IOF'
    assert detect_financial_report(text) is None


def test_duplicate_ocr_evidence_at_the_same_ink_region_does_not_duplicate_a_financial_value():
    source = cdb_source()
    page = source.pages[0]
    original = next(word for word in page.words if word.text == '110,00')
    duplicate = replace(original, id='duplicate-word')
    lines = [replace(line, word_ids=(*line.word_ids, duplicate.id)) if original.id in line.word_ids else line for line in page.lines]
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[
        replace(page, lines=lines, words=[*page.words, duplicate])]))
    assert result.transactions == []
    assert all(v['status'] == 'passed' for v in result.financial_document['validations'])


def test_multiple_position_periods_and_restarted_page_counters_preserve_each_snapshot():
    first = cdb_source().pages[0]
    second = replace(first, page_number=2,
        lines=[replace(line, page_number=2, text=line.text.replace('01/10/26 a 31/10/26', '01/11/26 a 30/11/26')) for line in first.lines],
        words=[replace(word, page_number=2) for word in first.words])
    pages = []
    for page in (first, second):
        counter = ExtractedLine('counter', page.page_number, 999, 'Página 1/1', bbox={'left': .8, 'top': .99, 'width': .1, 'height': .005})
        pages.append(replace(page, lines=[*page.lines, counter]))
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(cdb_source(), pages=pages))
    positions = [o for o in result.financial_document['observations'] if o['observation_type'] == 'fixed_income_position']
    assert len(positions) == 8
    assert {o['dates']['as_of'] for o in positions} == {'2026-10-31', '2026-11-30'}
    assert not result.parse_metrics.get('partial_statement')


def test_readable_table_continuation_without_repeated_headers_preserves_all_assets():
    source = cdb_source()
    first = source.pages[0]
    early = [line for line in first.lines if line.bbox['top'] < .28]
    rest = [line for line in first.lines if line not in early]
    early_ids = {word for line in early for word in line.word_ids}
    page1 = replace(first, lines=early, words=[word for word in first.words if word.id in early_ids])
    page2 = replace(first, page_number=2, lines=[replace(line, page_number=2) for line in rest],
                    words=[replace(word, page_number=2) for word in first.words if word.id not in early_ids])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[page1, page2]))
    positions = [o for o in result.financial_document['observations'] if o['observation_type'] == 'fixed_income_position']
    assert len(positions) == 4 and result.transactions == []
    assert {ref['page'] for o in positions for ref in o['source_refs']} == {1, 2}
    assert all(v['status'] == 'passed' for v in result.financial_document['validations'])


def test_two_legitimate_lots_of_the_same_asset_are_preserved_and_reconciled():
    cells = []
    half = {'100,0000': '50,0000', '100,00': '50,00', '110,00': '55,00', '2,00': '1,00', '108,00': '54,00'}
    for text, x, y in cdb_cells():
        if abs(y - .595) < .001:
            cells.extend((half.get(text, text), x, y + offset) for offset in (-.012, .012))
        else:
            cells.append((text, x, y))
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(cdb_source(cells))
    report = result.financial_document
    lots = [row for row in report['observations'] if row['observation_type'] == 'investment_lot']
    assert len(lots) == 5 and len([row for row in lots if row['attributes']['asset_code'] == ASSETS[0]]) == 2
    assert report['metadata']['position_count'] == 4 and result.transactions == []
    assert all(v['status'] == 'passed' for v in report['validations'])
