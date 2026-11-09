"""Fictional regressions for optional references and fragmented statement controls."""
import pytest

from app.application import pdf_parser
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_remaining_recovery_cases import bb_optional_reference_cells
from tests.test_sicoob_channel_pending import parse_pages
from tests.test_sicoob_statement_context import sisbr_pending_cells
from tests.test_wise_statements import convert, wise_lines


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_bb_empty_reference_under_printed_document_heading(representation):
    cells = bb_optional_reference_cells() + [("Documento", .63, .1)]
    result = parse_pages([cells], representation)
    assert [t.amount for t in result.transactions] == [50]
    assert result.canonical_transactions[0].external_reference_id is None
    assert result.parse_metrics["financial_controls_validated"] == 1


def test_wise_card_context_and_wrapped_reference_keep_every_movement():
    lines = wise_lines()
    lines = [line.replace("| Transação:", "| Cartão terminado em 1234 | TITULAR FICTÍCIO | Transação:") for line in lines]
    result = convert(lines)
    assert result.transactions_total == 6
    assert [t.amount for t in result.report_transactions] == [40, -40, 900, 450, 30, -1378.5]


def test_wise_balance_disagreement_preserves_explicit_movements_for_review():
    lines = [line.replace("30,00 1.380,00", "30,00 1.381,00") for line in wise_lines()]
    result = convert(lines)
    assert result.transactions_total == 6
    assert result.pdf_processing_metrics["financial_controls_validated"] == 0
    assert result.pdf_processing_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_sisbr_opening_on_first_movement_date(representation):
    pages = [[("01/07" if text == "30/06" else text,x,y) for text,x,y in p] for p in sisbr_pending_cells()]
    result = parse_pages(pages, representation)
    assert [t.amount for t in result.transactions] == [50, 200, -20]
    assert result.parse_metrics["financial_controls_validated"] == 1


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_sisbr_summary_can_continue_on_next_page(representation):
    first, second = sisbr_pending_cells()
    summary_y = next(y for text,x,y in second if text == "RESUMO")
    third = [(text,x,y-summary_y+.05) for text,x,y in second if y>summary_y]
    second = [cell for cell in second if cell[2]<=summary_y]
    result = parse_pages([first, second, third], representation)
    assert [t.amount for t in result.transactions] == [50, 200, -20]
    assert result.parse_metrics["verified_closing_balance"] == 330


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_sisbr_judicial_blocked_opening_is_not_cash(representation):
    pages = sisbr_pending_cells()
    pages[0].extend([("30/06", .19, .31), ("SALDO BLOQ.JUD.ANT.", .26, .31), ("700,00C", .75, .31)])
    result = parse_pages(pages, representation)
    assert [t.amount for t in result.transactions] == [50, 200, -20]
    assert result.parse_metrics["verified_opening_balance"] == 100


def test_c6_compact_month_totals_and_inline_rows_preserve_month_scopes():
    from tests.test_audited_financial_extractions import C6
    text = C6.replace("Abril 2026 (30/04/2026 30/04/2026)\nEntradas: R$ 100,00\nSaídas: R$ 20,00",
                      "Abril 2026(30/04/2026 - 30/04/2026) Entradas: R$ 100,00 • Saídas: R$ 20,00")
    text = text.replace("30/04\n30/04\nEntradas\nRESGATE DE CDB\nR$ 100,00", "30/04 30/04 Entradas RESGATE DE CDB R$ 100,00")
    result = pdf_parser._parse_pdf_transactions_from_page_texts([text])
    assert [r.amount for r in result.transactions] == [100, -20, 50, -10]
    assert [r.date for r in result.transactions] == ["2026-04-30"]*2 + ["2026-05-11"]*2


def test_itau_automatic_investment_alias_is_a_control_not_cash():
    from tests.test_bradesco_itau_readable_tables import itau_pages
    pages = itau_pages()
    pages[1] += [("02/10/2026", .05, .39), ("SDO APLIC AUT MAIS AP", .18, .39), ("500,00", .92, .39),
                 ("02/10/2026", .05, .45), ("SALDO TOTAL DISPONÍVEL DIA", .18, .45), ("620,00", .92, .45)]
    result = pdf_parser.parse_pdf_transactions(grid_pdf(pages))
    assert [r.amount for r in result.transactions] == [20, 20, -5, -15]
    assert result.parse_metrics["verified_closing_balance"] == 120


def test_sicredi_table_header_is_located_by_labels_not_top_margin():
    from dataclasses import replace

    from app.application.parsers.pdf.sicredi_visual_cash import parse_sicredi_visual_cash
    from tests.test_oct28_reviewed_geometry import sicredi_visual_source
    source = sicredi_visual_source()
    def shifted(item):
        return replace(item, bbox={**item.bbox, "top": item.bbox["top"]+.2})
    source = replace(source,pages=[replace(p,lines=[shifted(line) for line in p.lines],
                                          words=[shifted(w) for w in p.words]) for p in source.pages])
    result = parse_sicredi_visual_cash(source)
    assert [r.transaction.amount for r in result.rows] == [50]
    assert result.opening == 100 and result.closing == 150


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_sisbr_complete_empty_table_with_same_opening_and_closing(representation):
    first, second = sisbr_pending_cells()
    first = [c for c in first if c[2] <= .28]
    first = [("30/07" if text == "30/06" else text,x,y) for text,x,y in first]
    first += [("RESUMO", .45, .60), ("(+) SALDO EM CONTA:", .19, .64), ("100,00C", .75, .64)]
    result = parse_pages([first], representation)
    assert result.transactions == []
    assert result.parse_metrics["statement_no_activity"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 100


def test_itau_monthly_cover_precedes_cash_table_and_preserves_page_number():
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter

    from tests.test_itau_monthly_native_controls import monthly_pdf
    writer = PdfWriter()
    cover = grid_pdf([[("extrato mensal ag 1234 cc 56789-0 set 2026 001|002",.05,.05),
                       ("Conta Corrente e Aplicações Automáticas",.05,.10),
                       ("saldo consolidado em 30/09/26",.05,.15), ("R$ 1.150,00",.5,.15)]])
    for raw in [cover, monthly_pdf(cash_only=True)]:
        for page in PdfReader(BytesIO(raw)).pages:
            writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    result = pdf_parser.parse_pdf_transactions(output.getvalue())
    assert [t.amount for t in result.transactions] == [200,-50]
    assert {t.source_page for t in result.canonical_transactions} == {2}


def test_bb_savings_shifted_columns_and_early_table_header():
    cells = [("Extratos - Poupança POUPANÇA-OURO",.1,.05),
             ("Dt. lançamento",.096,.20),("Dt. base",.198,.20),("Histórico",.30,.20),
             ("Valor",.77,.20),("Saldo",.85,.20),
             ("31/08/2026",.096,.23),("Saldo anterior",.30,.23),("100,00 C",.85,.23),
             ("01/09/2026",.096,.26),("31/08/2026",.198,.26),("615 Pix recebido",.30,.26),("50,00",.77,.26),("C",.82,.26),
             ("Saldo atual",.096,.35),("Saldo total",.096,.40),("150,00",.58,.40),("C",.67,.40)]
    result = pdf_parser.parse_pdf_transactions(grid_pdf([cells]))
    assert [t.amount for t in result.transactions] == [50]
    assert result.transactions[0].date == "2026-09-01"
    assert result.parse_metrics["verified_closing_balance"] == 150


def test_wise_footer_only_last_page_does_not_drop_card_movements():
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
    lines = wise_lines()
    pages = [lines[:-2], ["ref:synthetic-wise 2 / 2", *lines[-2:]]]
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf(tuple(tuple(p) for p in pages)))
    assert len(result.transactions) == 6


def test_sisbr_opening_cannot_be_later_than_first_transaction():
    from app.application.errors import PdfFinancialValidationError
    pages = [[("20/07" if text == "30/06" else text,x,y) for text,x,y in p] for p in sisbr_pending_cells()]
    with pytest.raises(PdfFinancialValidationError):
        parse_pages(pages,"native")


def test_pagbank_native_uses_the_same_subaccount_contract_as_ocr():
    from tests.test_recovery_sweep_ocr import pagbank
    source = pagbank()
    cells = [(line.text,line.bbox["left"],line.bbox["top"]) for line in source.pages[0].lines]
    result = pdf_parser.parse_pdf_transactions(grid_pdf([cells]))
    assert len(result.financial_document["events"]) == 4
    assert len(result.financial_document["observations"]) == 2
    assert {e["attributes"]["subaccount"] for e in result.financial_document["events"]} == {"available","receivables"}
    assert result.financial_document["export"]["available_formats"] == ["csv","xlsx"]


def test_cora_old_daily_groups_keep_repeated_payments_and_printed_controls():
    from app.application.parsers.pdf.cora_daily_groups import parse_cora_daily_groups
    from app.application.parsers.pdf.models import _PdfLine
    texts = ["Cora pagamentos LTDA", "Saldo inicial do período R$ 100,00", "Total de entradas + R$ 40,00",
             "Total de saídas - R$ 5,00", "Saldo final do período R$ 135,00",
             "02/10/2026 Saldo do dia R$ 135,00", "- R$ 5,00Transf Pix enviada TESTE",
             "01/10/2026 Saldo do dia R$ 140,00", "+ R$ 20,00Transf Pix recebida TESTE",
             "+ R$ 20,00Transf Pix recebida TESTE", "Transações", "Extrato do período"]
    rows = parse_cora_daily_groups([_PdfLine(t,1,i) for i,t in enumerate(texts,1)])
    assert [r.transaction.amount for r in rows] == [20,20,-5]
    assert [r.running_balance for r in rows] == [None,140,135]
    assert len({r.source_line for r in rows}) == 3


@pytest.mark.parametrize("fragmented", [False,True])
def test_btg_summary_supports_fragmented_metadata_and_unknown_opening(fragmented):
    from tests.test_btg_summary_statement import btg_summary_pages
    pages = btg_summary_pages()
    pages = [p.replace("Saldo de abertura 04/06/2026 R$ 0,00","Saldo de abertura 04/06/2026 -")
             .replace("04/06/2026 Saldo de abertura R$ 0,00","04/06/2026 Saldo de abertura -") for p in pages]
    if fragmented:
        pages = [p.replace("Saldo de fechamento 05/06/2026 R$ 0,00","Saldo de fechamento\n05/06/2026\nR$ 0,00")
                 .replace("Total de entradas R$ 155,00","Total de entradas\nR$ 155,00") for p in pages]
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf(tuple(tuple(p.splitlines()) for p in pages)))
    assert len(result.transactions) == 13
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["financial_controls_validated"] == 0


@pytest.mark.parametrize("bank", ["bb", "sicredi"])
@pytest.mark.parametrize("extra", [False, True])
def test_empty_native_table_requires_positive_evidence_and_no_extra_row(bank, extra):
    from app.application.parsers.pdf.inactive_cash_tables import parse_inactive_cash_table
    from app.application.parsers.pdf.native_table_source import read_native_table_source
    if bank == "bb":
        cells = [("Banco do Brasil", .1, .05), ("Histórico", .3, .2),
                 ("Valor R$", .7, .2), ("Saldo", .85, .2),
                 ("30/09/2026", .08, .25), ("Saldo anterior", .3, .25), ("100,00 C", .85, .25),
                 ("A conta não foi movimentada", .15, .4), ("Invest. Resgate Autom.", .1, .5)]
    else:
        cells = [("SICREDI Período: de 01/10/2026 a 31/10/2026", .05, .05),
                 ("Débito", .6, .2), ("Crédito", .73, .2), ("Saldo", .88, .2),
                 ("*** Saldo anterior", .2, .25), ("0,00", .88, .25),
                 ("Saldo atual", .2, .5), ("0,00", .88, .5)]
    if extra:
        cells += [("01/10/2026", .08, .32), ("PIX", .3, .32), ("10,00 C", .7, .32)]
    raw = grid_pdf([cells])
    result = parse_inactive_cash_table(raw, read_native_table_source(raw))
    if extra:
        assert result is None
    else:
        assert result.rows == []
        assert result.opening == result.closing == (100 if bank == "bb" else 0)


@pytest.mark.parametrize("left,shade,height,accepted", [
    (0, .94, 5, True), (100, .94, 5, False), (0, .4, 5, False), (0, .94, 40, False),
])
def test_native_blank_page_only_tolerates_tiny_faint_scanner_edge(left, shade, height, accepted):
    from io import BytesIO

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    from app.application.errors import InvalidFileContentError
    from app.application.parsers.pdf.native_table_source import read_native_table_source
    output = BytesIO()
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    stream = DecodedStreamObject()
    stream.set_data(f"{shade} g {left} 100 .5 {height} re f".encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(output)
    if accepted:
        result = read_native_table_source(output.getvalue())
        assert len(result.pages) == 1 and result.pages[0].page_number == 1
        assert result.pages[0].lines == []
    else:
        with pytest.raises(InvalidFileContentError):
            read_native_table_source(output.getvalue())


def test_bradesco_independent_block_starting_on_same_physical_page(monkeypatch):
    import tests.test_audited_financial_extractions as fixtures
    original = grid_pdf

    def combine(pages):
        # Keep the first section, then fit the second header and opening
        # underneath its title. The second movement continues on page two.
        first, second = pages
        early = [(t,x,.59+(y-.12)*.6) for t,x,y in second if y <= .28]
        later = [(t,x,.15+(y-.34)) for t,x,y in second if y >= .34]
        return original([first+early, later])

    monkeypatch.setattr(fixtures, "grid_pdf", combine)
    result = pdf_parser.parse_pdf_transactions(fixtures.bradesco_continuation())
    assert [t.amount for t in result.transactions] == [50,20,20]
    assert [t.source_page for t in result.canonical_transactions] == [1,1,2]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_cora_unreadable_signed_movement_is_not_omitted():
    from app.application.errors import InvalidFileContentError
    from app.application.parsers.pdf.cora_daily_groups import parse_cora_daily_groups
    from app.application.parsers.pdf.models import _PdfLine
    with pytest.raises(InvalidFileContentError):
        parse_cora_daily_groups([_PdfLine("01/10/2026 Saldo do dia R$ 100,00",1,1),
                                 _PdfLine("- R$ ilegível Pix enviado",1,2)])
