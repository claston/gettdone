"""Synthetic signed BB and Banpará columns, with independent printed controls."""

from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from tests.test_oct20_followup_ocr import ocr


def bb_signed():
    return ocr([[
        ("Extrato de Conta Corrente", .1, .02),
        ("Dia", .08, .1), ("Lote", .2, .1), ("Documento", .3, .1),
        ("Histórico", .45, .1), ("Valor", .9, .1),
        ("30/06/2026", .08, .15), ("Saldo Anterior", .45, .15), ("100,00 (+)", .9, .15),
        ("01/07/2026", .08, .21), ("99015", .2, .21), ("10001", .3, .21),
        ("Pix Recebido", .45, .20), ("CLIENTE TESTE", .45, .22), ("50,00 (+)", .9, .21),
        ("02/07/2026", .08, .27), ("13105", .2, .27), ("10002", .3, .27),
        ("Pagamento de Boleto", .45, .26), ("20,00 (-)", .9, .27),
        ("00/00/0000", .08, .33), ("Saldo do dia", .45, .33), ("130,00 (+)", .9, .33),
        ("02/07/2026", .08, .4), ("Saldo", .45, .4), ("130,00 (+)", .9, .4),
    ]])


def banpara_columns():
    return ocr([[
        ("Banpará Banco do Estado do Pará", .1, .02),
        ("PERIODO: 01/07/2026 A 31/07/2026", .1, .05),
        ("SALDO ANTERIOR", .08, .12), ("100,00", .85, .12),
        ("Data Descricao", .08, .16), ("Doc.", .48, .16), ("Valor", .64, .16), ("Saldo", .85, .16),
        ("01/07 PGTO. TESTE", .08, .25), ("10001", .48, .25), ("-20,00", .64, .25),
        ("Internet Banking", .13, .27),
        ("01/07 REMUN CONTA+", .08, .3), ("10002", .48, .3), ("0,01", .64, .3), ("80,01", .85, .3),
        ("02/07 CRED TED", .08, .4), ("10003", .48, .4), ("50,00", .64, .4), ("130,01", .85, .4),
        ("SALDO ATUAL", .08, .5), ("130,01", .85, .5),
    ]])


@pytest.mark.parametrize("factory,amounts,dates", [
    (bb_signed, [50, -20], ["2026-07-01", "2026-07-02"]),
    (banpara_columns, [-20, .01, 50], ["2026-07-01", "2026-07-01", "2026-07-02"]),
])
def test_printed_columns_preserve_amounts_dates_references_and_exclude_balances(factory, amounts, dates):
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(factory())
    assert [r.amount for r in result.transactions] == amounts
    assert [r.date for r in result.transactions] == dates
    assert all(row.external_reference_id for row in result.canonical_transactions)
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0


@pytest.mark.parametrize("factory,token", [(bb_signed, "20,00 (-)"), (banpara_columns, "-20,00")])
def test_unreadable_movement_amount_is_never_synthesized_from_balances(factory, token):
    source = factory()
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[replace(line, text="valor ilegível")
        if line.text == token else line for line in page.lines])])
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


def test_bb_duplicated_open_parenthesis_preserves_the_explicit_debit_sign():
    source = bb_signed()
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[replace(line, text="20,00 ( (-)")
        if line.text == "20,00 (-)" else line for line in page.lines])])
    assert [row.amount for row in pdf_parser._parse_scanned_pdf_with_textract_text_pages(source).transactions] == [50, -20]


def test_banpara_separate_date_and_description_cells_preserve_small_interest_credit():
    source = banpara_columns()
    page = source.pages[0]
    anchor = next(line for line in page.lines if line.text == "01/07 REMUN CONTA+")
    label = replace(anchor, id="separate-description", line_index=999, text="REMUN CONTA+",
                    bbox={**anchor.bbox, "left": .17})
    source = replace(source, pages=[replace(page, lines=[replace(line, text="01/07")
        if line is anchor else line for line in page.lines] + [label])])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [-20, .01, 50]
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_banpara_orphan_amount_without_a_readable_date_is_refused():
    source = banpara_columns()
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[replace(line, text="data ilegível REMUN CONTA+")
        if line.text == "01/07 REMUN CONTA+" else line for line in page.lines])])
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_bb_truncated_year_requires_a_unique_matching_printed_year(ambiguous):
    source = bb_signed()
    page = source.pages[0]
    lines = [replace(line, text="02/07/202") if line.text == "02/07/2026" else line for line in page.lines]
    if ambiguous:
        lines = [replace(line, text="30/06/2025") if line.text == "30/06/2026" else line for line in lines]
    source = replace(source, pages=[replace(page, lines=lines)])
    if ambiguous:
        with pytest.raises(InvalidFileContentError):
            pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    else:
        result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
        assert result.transactions[-1].date == "2026-07-02"
        assert "ocr_date_year_completed" in result.canonical_transactions[-1].warnings
