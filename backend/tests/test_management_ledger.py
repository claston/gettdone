"""Fictional management ledgers: original columns, arithmetic and coverage."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from app.application.parsers.pdf.native_table_source import read_native_table_source
from tests.fixtures.recovery_acceptance import grid_pdf


def ledger_page(*, title="Conta operacional", kind="Conta Corrente", period="01/10/2026 até 31/10/2026",
                number="1/1", opening="100,00", day="01/10", status="Conciliado", amount="-20,00", balance="80,00"):
    cells = [("Banco: 001", .70, .012), ("Extrato de " + title, .15, .03), (kind, .15, .055),
             ("Agência: 1234 / Conta: 00123-4", .15, .08),
             (f"Período de {period} (Página {number})", .34, .10)]
    cells += [(text, x, .125) for text, x in zip(
        ("Situação", "Data", "Cliente ou Fornecedor", "Documento", "Categoria", "Valor", "Saldo"),
        (.07, .165, .22, .435, .53, .825, .92), strict=True)]
    if opening is not None:
        cells += [("30/09" if "10/2026" in period else "31/10", .165, .15),
                  ("SALDO ANTERIOR", .22, .15), (opening, .92, .15)]
    cells += [(status, .07, .19), (day, .165, .19), ("Fornecedor de teste", .22, .19),
              ("000123", .435, .19), ("3.120 - Copa e Cozinha", .53, .19), (amount, .825, .19), (balance, .92, .19),
              ("Gerado em 10/11/2026 às 10:00:00 por Operador", .07, .95),
              ("Página " + number.replace("/", " de "), .80, .97)]
    return cells


def parse(pages, representation):
    raw = grid_pdf(pages)
    if representation == "native":
        return pdf_parser.parse_pdf_transactions(raw)
    source = replace(read_native_table_source(raw), provider="aws_textract")
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("title,kind,ofx", [
    ("Conta operacional", "Conta Corrente", True),
    ("CARTÃO BRADESCO", "Conta Corrente", False),
    ("Adquirente fictícia", "Administradora de Cartões", False),
])
def test_columns_and_semantics_survive(representation, title, kind, ofx):
    result = parse([ledger_page(title=title, kind=kind)], representation)
    document = result.financial_document
    assert document["document_type"] == "cash_flow_report"
    event, = document["events"]
    assert event["amount"] == "-20.00" and event["amounts"]["balance"] == "80.00"
    assert event["dates"] == {"transaction": "2026-10-01"}
    assert event["reference"] == "000123"
    assert event["counterparty"] == "Fornecedor de teste"
    assert event["attributes"]["category"] == "3.120 - Copa e Cozinha"
    assert event["attributes"]["printed_status"] == "Conciliado"
    assert all(check["status"] == "passed" for check in document["validations"])
    assert ("ofx" in document["export"]["available_formats"]) is ofx
    assert result.canonical_transactions[0].source_regions
    assert result.canonical_transactions[0].bank_name is None


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_continuation_and_legitimate_duplicates(representation):
    result = parse([ledger_page(number="1/2"), ledger_page(number="2/2", opening=None, balance="60,00")], representation)
    assert [row.amount for row in result.transactions] == [-20, -20]
    assert [row.source_page for row in result.canonical_transactions] == [1, 2]
    assert all(c["status"] == "passed" for c in result.financial_document["validations"])


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("period,opening,clean", [
    ("01/11/2026 até 30/11/2026", "80,00", True),
    ("01/11/2026 até 30/11/2026", "90,00", False),
    ("02/11/2026 até 30/11/2026", "80,00", False),
    ("31/10/2026 até 30/11/2026", "80,00", False),
])
def test_independent_months_restarted_counters_and_continuity(representation, period, opening, clean):
    result = parse([ledger_page(), ledger_page(period=period, opening=opening, day="02/11",
                   balance="70,00" if opening == "90,00" else "60,00")], representation)
    assert len(result.transactions) == 2
    checks = result.financial_document["validations"]
    assert all(c["status"] == "passed" for c in checks) is clean
    assert bool(result.parse_metrics.get("export_recommendation")) is not clean


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("change", [{"balance": "79,00"}, {"number": "1/2"}, {"opening": None}, {"status": "Previsto"}])
def test_readable_uncertainty_requires_review_and_blocks_ofx(representation, change):
    result = parse([ledger_page(**change)], representation)
    assert result.transactions[0].amount == -20
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.financial_document["export"]["available_formats"] == ["csv", "xlsx"]
    if "status" in change:
        assert result.financial_document["events"][0]["cash_effect"] is None


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("change", [{"amount": "-?0,00"}, {"day": "??/10"},
                                   {"period": "01/10/???? até 31/10/????"}, {"amount": "--20,00"}])
def test_ambiguous_fields_never_become_partial_success(representation, change):
    with pytest.raises(PdfFinancialValidationError):
        parse([ledger_page(), ledger_page(**change)], representation)


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_missing_continuation_header_preserves_readable_rows_with_review(representation):
    second = [cell for cell in ledger_page(number="2/2", opening=None, balance="60,00") if cell[2] > .125]
    result = parse([ledger_page(number="1/2"), second], representation)
    assert [row.amount for row in result.transactions] == [-20, -20]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_wrapped_original_fields_and_multiple_accounts(representation):
    first = ledger_page()
    first += [("Complemento", .22, .205), ("/2", .435, .205), ("Centro adicional", .53, .205)]
    second = [(text.replace('00123-4', '00567-8'), x, y)
              for text, x, y in ledger_page(title="Outra conta", opening="50,00", balance="30,00")]
    result = parse([first, second], representation)
    first_event, second_event = result.financial_document['events']
    assert first_event['counterparty'] == 'Fornecedor de teste Complemento'
    assert first_event['reference'] == '000123 /2'
    assert first_event['attributes']['category'] == '3.120 - Copa e Cozinha Centro adicional'
    assert len(first_event['source_refs']) > len(second_event['source_refs'])
    assert first_event['attributes']['block'] == 1 and second_event['attributes']['block'] == 2
    assert result.financial_document['export']['available_formats'] == ['csv', 'xlsx']


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_account_rename_does_not_hide_continuity_gap(representation):
    second = ledger_page(title='Conta com nome novo', period='02/11/2026 até 30/11/2026',
                         opening='80,00', day='02/11', balance='60,00')
    result = parse([ledger_page(), second], representation)
    assert len(result.transactions) == 2
    assert any(c['code'] == 'management_blocks_continuity' and c['status'] == 'failed'
               for c in result.financial_document['validations'])
    assert result.parse_metrics['export_recommendation'] == 'review_recommended'


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_unreadable_later_page_is_not_omitted(representation):
    with pytest.raises(PdfFinancialValidationError):
        parse([ledger_page(), [("Texto ilegível", .07, .20)]], representation)


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_later_header_without_readable_movements_is_not_partial_success(representation):
    empty = [cell for cell in ledger_page() if cell[2] != .19]
    with pytest.raises(PdfFinancialValidationError):
        parse([ledger_page(), empty], representation)


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_header_words_in_counterparty_are_not_a_table_header(representation):
    cells = [('Data Data Documento' if text == 'Fornecedor de teste' else text, x, y) for text, x, y in ledger_page()]
    event, = parse([cells], representation).financial_document['events']
    assert event['counterparty'] == 'Data Data Documento'


@pytest.mark.parametrize('representation', ['native', 'ocr'])
def test_long_counterparty_can_use_empty_document_space_without_creating_a_reference(representation):
    party = 'FORNECEDOR FICTÍCIO COM NOME COMERCIAL MUITO EXTENSO LTDA'
    cells = [(party if text == 'Fornecedor de teste' else text, x, y)
             for text, x, y in ledger_page() if text != '000123']
    event, = parse([cells], representation).financial_document['events']
    assert event['counterparty'] == party
    assert event['reference'] is None


def test_native_text_objects_preserve_overlapping_name_and_reference_but_ocr_cannot_guess():
    party = 'FORNECEDOR FICTÍCIO COM NOME COMERCIAL MUITO EXTENSO LTDA'
    cells = [(party if text == 'Fornecedor de teste' else text, x, y) for text, x, y in ledger_page()]
    event, = parse([cells], 'native').financial_document['events']
    assert event['counterparty'] == party and event['reference'] == '000123'
    source = read_native_table_source(grid_pdf([cells]), preserve_text_objects=True)
    # OCR has bounding boxes but cannot rely on the original PDF text objects.
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, provider='aws_textract', metrics={}))


def test_ocr_gateway_preserves_words_and_all_blocks(monkeypatch):
    pages = [ledger_page(), ledger_page(period='01/11/2026 até 30/11/2026', opening='80,00', day='01/11', balance='60,00')]
    raw = grid_pdf(pages)
    source = read_native_table_source(raw)
    blocks = []
    for page in source.pages:
        for item in [*page.words, *page.lines]:
            block = {'Id': item.id, 'Page': page.page_number, 'Text': item.text, 'Confidence': 99,
                     'Geometry': {'BoundingBox': {k.title(): v for k, v in item.bbox.items()}}}
            block['BlockType'] = 'LINE' if hasattr(item, 'word_ids') else 'WORD'
            if hasattr(item, 'word_ids'):
                block['Relationships'] = [{'Type': 'CHILD', 'Ids': list(item.word_ids)}]
            blocks.append(block)
    monkeypatch.setenv('TEXTRACT_ENABLED', 'true')
    monkeypatch.setenv('TEXTRACT_FORCE', 'true')
    monkeypatch.setenv('TEXTRACT_TEMP_BUCKET', 'unused-synthetic-bucket')
    monkeypatch.setattr(pdf_parser.TextractGateway, 'analyze_pdf', lambda *a, **kw: {
        'document_hash': 'synthetic', 'page_count': 2, 'blocks': blocks, 'metrics': {'textract_mode': 'text'}})
    result = pdf_parser.parse_pdf_transactions(raw)
    assert len(result.financial_document['events']) == 2
    assert result.parse_metrics['textract_used'] == 1
    assert all(check['status'] == 'passed' for check in result.financial_document['validations'])
    assert all(row.source_regions for row in result.canonical_transactions)
