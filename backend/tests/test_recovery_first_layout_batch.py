"""Synthetic reproductions of the November recovery batch; no customer data."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.document_extraction_models import ExtractedWord
from app.application.errors import PdfFinancialValidationError
from app.application.parsers.pdf.native_cash_columns import _summary
from app.application.parsers.pdf.statement_blocks import statement_blocks
from tests.fixtures.positioned_cash_statements import positioned_pdf, santander_pages
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_oct28_new_statement_failures import banrisul_month_source, bb_duplicate_opening
from tests.test_pdf_parser_bradesco_card_web import native_pdf, prohibit_ocr, statement_pages
from tests.test_recovery_sweep_ocr import extraction


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def test_bb_right_aligned_balance_with_overlapping_printed_sign():
    source = bb_duplicate_opening(duplicate=False)
    page = source.pages[0]
    lines, words = [], []
    for line in page.lines:
        if line.text == "Saldo":
            line = replace(line, bbox={**line.bbox, "left": .894})
        if line.text == "150,00 C":
            number = ExtractedWord(line.id + "-number", 1, len(words)+1, "150,00", 99,
                                   {**line.bbox, "left": .859, "width": .054}, "PRINTED")
            sign = ExtractedWord(line.id + "-sign", 1, len(words)+2, "C", 99,
                                 {**line.bbox, "left": .912, "width": .009}, "PRINTED")
            words.extend([number, sign])
            line = replace(line, bbox={**line.bbox, "left": .859, "width": .062}, word_ids=(number.id, sign.id))
        lines.append(line)
    source = replace(source, pages=[replace(page, lines=lines, words=words)])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [50]
    assert result.parse_metrics["verified_closing_balance"] == 150
    assert result.source_extraction == source


CONTACT = """Demais telefones
consulte o site
Fale Conosco
Ouvidoria 0800 727 9933 Atendimento de segunda a sexta-feira, das 8h às 18h, exceto feriados.
Cancelamentos, Reclamações e
Informações. Atendimento 24 horas, 7 dias
por semana.
Deficiente Auditivo ou de Fala
0800 722 0099
Alô Bradesco
0800 704 8383
SAC - Serviço de
Apoio ao Cliente
"""


def test_card_footer_before_last_movements_preserves_rows_and_source(monkeypatch):
    prohibit_ocr(monkeypatch)
    pages = statement_pages()
    first, rest = pages[0].split("03/01 LOJA", 1)
    result = pdf_parser.parse_pdf_transactions(native_pdf([first, CONTACT + "03/01 LOJA" + rest, pages[1]]))
    assert [row.amount for row in result.transactions] == [-125.4, -60, -18.9, -40, -75, -30, -50]
    assert [row.source_page for row in result.canonical_transactions] == [1]*4 + [2]*3
    assert all(row.running_balance is None for row in result.canonical_transactions)


@pytest.mark.parametrize("bad", ["03/01 COMPRA 0,00 9X,00", "Lançamento ilegível", "TOTAL: 0,00 9,00"])
def test_card_footer_does_not_hide_invalid_financial_content(bad):
    pages = statement_pages()
    pages[0] = pages[0].replace("03/01 LOJA", CONTACT + bad + "\n03/01 LOJA")
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_pdf_transactions_from_page_texts(pages)


def test_optional_santander_summary_does_not_replace_printed_ledger_controls():
    _, _, controls = _summary("Santander Extrato Consolidado agosto/2026 EXTRATO_PJ_A4_BASICO\n"
                              "SALDO DISPONIVEL DE CONTA CORRENTE EM 31/08 0,00\n"
                              "SALDO EM 31/07 83,04\nSALDO EM 31/08 0,00")
    assert controls == {"opening": None, "closing": Decimal("0"), "credit": None, "debit": None}


def test_full_month_statement_headers_split_without_using_investment_history():
    def page(month, extra=""):
        return f"EXTRATO CONSOLIDADO INTELIGENTE\n{month}/2026\nSANTANDER\nConta corrente\n{extra}"
    blocks = statement_blocks([page("março"), "INVESTIMENTOS\nJULHO / 2026", page("abril"), page("agosto"), page("fevereiro")])
    assert [(b.start, b.stop, b.period[0]) for b in blocks] == [
        (0, 2, "2026-03-01"), (2, 3, "2026-04-01"), (3, 4, "2026-08-01"), (4, 5, "2026-02-01")]


def banrisul_two_months(*, month="MAI", repeated_opening=False):
    source = banrisul_month_source()
    cells = [(line.text, line.bbox["left"], line.bbox["top"]) for line in source.pages[0].lines]
    if repeated_opening:
        cells += [("SALDO ANT EM 30/04/2026", .11, .48), ("130,01", .60, .48)]
    cells += [(f"++ MOVIMENTOS {month}/2026", .11, .51), ("01", .06, .55),
              ("PIX TESTE", .11, .55), ("100003", .35, .55), ("10,00-", .60, .55),
              ("SALDO NA DATA", .11, .59), ("120,01", .60, .59)]
    return extraction(cells)


def parse_banrisul(source, representation):
    if representation == "native":
        return pdf_parser.parse_pdf_transactions(grid_pdf([
            [(line.text, line.bbox["left"], line.bbox["top"]) for line in page.lines] for page in source.pages]))
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("repeated_opening", [False, True])
def test_banrisul_month_changes_inside_a_page_preserve_all_movements(repeated_opening, representation):
    result = parse_banrisul(banrisul_two_months(repeated_opening=repeated_opening), representation)
    assert [row.amount for row in result.transactions] == [-20, 50, .01, -10]
    assert [row.date for row in result.transactions] == ["2026-04-01", "2026-04-01", "2026-04-02", "2026-05-01"]
    assert all(row.source_page == 1 for row in result.canonical_transactions)
    assert len(result.document_parts) == 2
    assert [part["opening_balance"] for part in result.document_parts] == [100, 130.01]
    assert result.document_parts[-1]["period"]["end_date"] == "2026-05-01"
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert len(result.source_page_texts) == 1


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_banrisul_gap_is_reviewed_without_a_global_validated_balance(representation):
    result = parse_banrisul(banrisul_two_months(month="JUL"), representation)
    assert len(result.transactions) == 4
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert all(part["requires_review"] for part in result.document_parts)
    data = build_default_conversion_pipeline().run(filename="synthetic.pdf", raw_bytes=b"fixture",
        analysis_id="an_batch_gap", pdf_parser=lambda *args, **kwargs: result).analysis_data
    assert data.opening_balance is data.closing_balance is None


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_banrisul_future_months_are_excluded_only_after_explicit_section_heading(representation):
    source = banrisul_two_months()
    cells = [(line.text, line.bbox["left"], line.bbox["top"]) for line in source.pages[0].lines]
    cells += [("MOVIMENTOS FUTUROS DA CONTA CORRENTE", .06, .65), ("++ MOVIMENTOS JAN/2027", .11, .68),
              ("05", .06, .72), ("PIX AGENDADO", .11, .72), ("100004", .35, .72), ("99,00-", .60, .72)]
    result = parse_banrisul(extraction(cells), representation)
    assert [row.amount for row in result.transactions] == [-20, 50, .01, -10]
    assert result.parse_metrics["scheduled_entries_excluded"] == 1
    assert "scheduled_transactions_excluded" in result.canonical_transactions[-1].warnings


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("before,after", [("10,00-", "1?,00-"), ("MAI/2026", "???/2026")])
def test_banrisul_unreadable_later_month_is_not_dropped(representation, before, after):
    source = banrisul_two_months()
    source = replace(source, pages=[replace(page, lines=[replace(line, text=line.text.replace(before, after))
                                                       for line in page.lines]) for page in source.pages])
    with pytest.raises(PdfFinancialValidationError):
        parse_banrisul(source, representation)


def test_banrisul_same_page_keeps_existing_movement_source_references():
    result = parse_banrisul(banrisul_month_source(), "ocr")
    expected = [(13, 12, 15, 14), (17, 16, 18), (23, 22, 24)]
    assert [row.source_line_refs for row in result.canonical_transactions] == [
        tuple((1, line) for line in lines) for lines in expected
    ]
    assert [len(row.source_regions) for row in result.canonical_transactions] == [4, 3, 3]


def test_banrisul_date_context_on_previous_page_keeps_original_provenance():
    source = banrisul_two_months()
    page = source.pages[0]
    split = .55
    first = replace(page, lines=[line for line in page.lines if line.bbox["top"] <= split and line.text != "10,00-"])
    # Keep the full final movement on the next page, with the printed month/day
    # remaining on the preceding page. The transaction must refer to both.
    first = replace(first, lines=[line for line in first.lines if line.text not in {"PIX TESTE", "100003"}])
    second = replace(page, page_number=2, lines=[replace(line, id="p2-"+line.id, page_number=2,
        bbox={**line.bbox, "top": line.bbox["top"]-.45}) for line in page.lines
        if line.bbox["top"] >= split and line.text != "01"])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[first, second]))
    assert result.transactions[-1].date == "2026-05-01"
    assert result.canonical_transactions[-1].source_page == 2
    assert {page for page, _ in result.canonical_transactions[-1].source_line_refs} == {1, 2}
    assert {region.page for region in result.canonical_transactions[-1].source_regions} == {1, 2}


def santander_basic_pages(*, missing_opening=True):
    result = []
    for page in santander_pages():
        cells = []
        for x, y, text in page:
            if missing_opening and y == 725:
                continue
            text = text.replace("INTELIGENTE", "").replace("Inteligente", "Basico")
            text = text.replace("Saldo de Conta Corrente", "Saldo Disponivel de Conta Corrente")
            cells.append((x, y, text))
        result.append(cells)
    return result


@pytest.mark.parametrize("missing_opening", [False, True])
def test_santander_native_ledger_validates_with_optional_summary_opening(missing_opening):
    result = pdf_parser.parse_pdf_transactions(positioned_pdf(santander_basic_pages(missing_opening=missing_opening)))
    assert [row.amount for row in result.transactions] == [23.78, 257.47, 18.75, -300]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_opening_balance"] == result.parse_metrics["verified_closing_balance"] == 0


def test_santander_inconsistent_optional_summary_requires_review():
    pages = santander_basic_pages(missing_opening=False)
    pages[0] = [(x, y, "1,00" if x == 400 and y == 725 else text) for x, y, text in pages[0]]
    result = pdf_parser.parse_pdf_transactions(positioned_pdf(pages))
    assert len(result.transactions) == 4
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_santander_unreadable_present_summary_is_not_treated_as_absent():
    pages = santander_basic_pages(missing_opening=False)
    pages[0] = [(x, y, "0,?0" if x == 400 and y == 725 else text) for x, y, text in pages[0]]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(positioned_pdf(pages))


@pytest.mark.parametrize("unreadable", [False, True])
def test_santander_cover_starts_a_new_scope_before_summary_and_transactions(unreadable):
    pages = [[(25, 780, "EXTRATO CONSOLIDADO INTELIGENTE"), (25, 760, "fevereiro/2026")], *santander_pages()]
    second = [[(x, y, text.replace("fevereiro", "abril").replace("31/01", "31/03").replace("28/02", "30/04")
               .replace("04/02", "04/04")) for x, y, text in page] for page in pages]
    if unreadable:
        second[2] = [(x, y, text.replace("300,00-", "30?,00-")) for x, y, text in second[2]]
        with pytest.raises(PdfFinancialValidationError):
            pdf_parser.parse_pdf_transactions(positioned_pdf(pages + second))
        return
    result = pdf_parser.parse_pdf_transactions(positioned_pdf(pages + second))
    assert len(result.transactions) == 8
    assert [row.source_page for row in result.canonical_transactions] == [2, 2, 3, 3, 6, 6, 7, 7]
    assert result.parse_metrics["statement_blocks_count"] == 2
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert result.parse_metrics["export_recommendation_reason"] == "statement_blocks_not_contiguous"
