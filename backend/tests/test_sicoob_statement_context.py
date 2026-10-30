"""Fictional Sicoob ledgers exercise period, direction and pending cash semantics."""

import calendar
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_recovery_sweep_ocr import extraction


def modern_cells(*, descending=False, month=7, year=2026, opening=100, same_day=False,
                 period=None, expiry=True, mismatch=False):
    period = period or f"01/{month:02}/{year} - {calendar.monthrange(year, month)[1]:02}/{month:02}/{year}"
    cells = [
        ("SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", .05, .02),
        ("PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR", .05, .05),
        ("EXTRATO DE CONTA CORRENTE", .05, .08),
        ("Cooperativa: 1234-0 / SICOOB FICTÍCIO", .05, .11),
        ("Conta: 12345-6 / EMPRESA FICTÍCIA", .05, .14),
        ("Período:", .05, .17), (period, .55, .17),
        ("Data", .05, .21), ("Documento", .12, .21), ("Histórico", .24, .21), ("Valor", .9, .21),
    ]
    first, second = f"01/{month:02}", f"{1 if same_day else 2:02}/{month:02}"
    # Identical legitimate payments must both survive extraction and ordering.
    ledger = [
        (f"30/{month-1:02}", "SALDO ANTERIOR", f"R$ {opening},00C"),
        (first, "PIX RECEBIDO", "R$ 50,00C"),
        (first, "SALDO DO DIA", f"R$ {opening+50},00C"),
        (second, "PAGAMENTO TESTE", "R$ 10,00D"),
        (second, "PAGAMENTO TESTE", "R$ 10,00D"),
        (second, "SALDO DO DIA", f"R$ {opening+30+int(mismatch)},00C"),
    ]
    if same_day:
        ledger.pop(2)  # Only one closing control for that day.
    for i, (date, history, value) in enumerate(reversed(ledger) if descending else ledger):
        y = .27 + i * .055
        cells += [(date, .05, y), (history, .24, y), (value, .86, y)]
    cells += [("RESUMO", .24, .65), ("Saldo em conta:", .24, .69), (f"R$ {opening+30},00C", .86, .69)]
    if expiry:
        cells += [("Vencimento do cheque especial:", .05, .75), ("03/05/2027", .85, .75)]
    cells += [("Página 1/1", .05, .94)]
    return cells


def parse_cells(cells, representation):
    if representation == "native":
        return pdf_parser.parse_pdf_transactions(grid_pdf([cells]))
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction(cells))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("descending,same_day", [(False, False), (True, False), (False, True), (True, True)])
def test_period_and_printed_direction_preserve_dates_duplicates_and_provenance(representation, descending, same_day):
    result = parse_cells(modern_cells(descending=descending, same_day=same_day), representation)
    assert result.layout.layout_name == "sicoob_modern_printed_cash_columns_v1"
    assert [t.amount for t in result.transactions] == [50, -10, -10]
    assert [t.date for t in result.transactions] == ["2026-07-01", *(["2026-07-01"] * 2 if same_day else ["2026-07-02"] * 2)]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert all(t.source_regions and t.source_line_refs for t in result.canonical_transactions)


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_december_january_context_resolves_each_abbreviated_date(representation):
    cells = modern_cells(period="01/12/2025 - 31/01/2026")
    cells = [("31/12" if text == "01/07" else "02/01" if text == "02/07" else text, x, y) for text, x, y in cells]
    result = parse_cells(cells, representation)
    assert [t.date for t in result.transactions] == ["2025-12-31", "2026-01-02", "2026-01-02"]
    assert result.parse_metrics["financial_controls_validated"] == 1


@pytest.mark.parametrize("period", ["01/07/2026 31/07/2026", "01/07/2026 a 31/07/2026"])
def test_readable_period_survives_a_missing_or_textual_range_separator(period):
    result = parse_cells(modern_cells(period=period), "ocr")
    assert [t.date for t in result.transactions] == ["2026-07-01", "2026-07-02", "2026-07-02"]
    assert result.parse_metrics["financial_controls_validated"] == 1


@pytest.mark.parametrize("period", ["01/07/2025 - 31/08/2026", "01/08/2026 - 31/08/2026", "31/07/2026 - 01/07/2026"])
def test_period_cannot_invent_an_ambiguous_or_outside_transaction_year(period):
    with pytest.raises(PdfFinancialValidationError):
        parse_cells(modern_cells(period=period), "ocr")


def test_conflicting_labelled_periods_remain_ambiguous():
    cells = modern_cells() + [("Período: 01/07/2025 - 31/07/2025", .05, .79)]
    with pytest.raises(PdfFinancialValidationError):
        parse_cells(cells, "ocr")


def test_genuinely_missing_year_is_not_taken_from_the_current_date():
    cells = [(text, x, y) for text, x, y in modern_cells(expiry=False) if "/2026" not in text]
    with pytest.raises(PdfFinancialValidationError):
        parse_cells(cells, "ocr")


def test_an_expiry_year_alone_cannot_date_abbreviated_transactions():
    cells = [(text, x, y) for text, x, y in modern_cells() if "/2026" not in text]
    with pytest.raises(PdfFinancialValidationError):
        parse_cells(cells, "ocr")


def test_full_transaction_dates_remain_readable_without_optional_period():
    cells = [(text + "/2026" if text in {"01/07", "02/07"} else text, x, y)
             for text, x, y in modern_cells() if "/2026" not in text]
    result = parse_cells(cells, "ocr")
    assert [t.amount for t in result.transactions] == [50, -10, -10]
    assert result.parse_metrics["financial_controls_validated"] == 1


def test_mixed_date_direction_is_not_silently_sorted():
    cells = modern_cells(expiry=False)
    cells += [("01/07", .05, .62), ("PAGAMENTO TESTE", .24, .62), ("R$ 10,00D", .86, .62)]
    with pytest.raises(PdfFinancialValidationError):
        parse_cells(cells, "ocr")


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_readable_modern_balance_divergence_is_exportable_with_review(representation):
    result = parse_cells(modern_cells(mismatch=True), representation)
    assert [t.amount for t in result.transactions] == [50, -10, -10]
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("month,opening,contiguous", [(8, 130, True), (9, 130, False), (8, 131, False)])
def test_month_blocks_with_restarted_pagination_keep_all_source_pages(representation, month, opening, contiguous):
    first, second = modern_cells(), modern_cells(month=month, opening=opening, descending=True)
    if representation == "native":
        result = pdf_parser.parse_pdf_transactions(grid_pdf([first, second]))
    else:
        source = extraction(first)
        result = pdf_parser._parse_textract_text_pages(replace(source, pages=[source.pages[0], extraction(second, page=2).pages[0]]))
    assert [t.amount for t in result.transactions] == [50, -10, -10] * 2
    assert {t.source_page for t in result.canonical_transactions} == {1, 2}
    assert result.parse_metrics["statement_blocks_count"] == 2
    assert result.parse_metrics["financial_controls_validated"] == int(contiguous)
    assert all(t.source_regions and t.source_line_refs for t in result.canonical_transactions)
    if not contiguous:
        assert result.parse_metrics["export_recommendation"] == "review_recommended"
        assert result.parse_metrics["balance_bounds_unavailable"] == 1


def sisbr_pending_cells(*, marker="*", history="DEP.CH.BLOQ.1D-INTE", idle_balance="150,00C", missing_control=False):
    first = [
        ("SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", .19, .03),
        ("PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR", .19, .06),
        ("EXTRATO CONTA CORRENTE", .35, .09), ("COOP.: 1234-0 / SICOOB TESTE", .19, .12),
        ("CONTA: 12345-6 / EMPRESA FICTÍCIA", .19, .15),
        ("PERÍODO: 01/07/2026 - 31/07/2026", .19, .18),
        ("HISTÓRICO DE MOVIMENTAÇÃO", .25, .21),
        ("DATA", .19, .24), ("HISTÓRICO", .26, .24), ("VALOR", .75, .24),
        ("30/06", .19, .28), ("SALDO ANTERIOR", .26, .28), ("100,00C", .75, .28),
        ("01/07", .19, .34), ("PIX RECEBIDO", .26, .34), ("50,00C", .75, .34), ("DOC.: 1001", .26, .36),
        ("01/07", .19, .40), ("SALDO DO DIA", .26, .40), ("150,00C", .75, .40),
        ("02/07", .19, .46), (history, .26, .46), ("200,00", .75, .46),
        (marker, .81, .475), ("DOC.: 1002", .26, .49),
        ("02/07", .19, .54), ("SALDO DO DIA", .26, .54), (idle_balance, .75, .54),
    ]
    second = [
        ("03/07", .19, .08), ("LIBER.DEPÓSITO BLOQ", .26, .08), ("200,00C", .75, .08), ("DOC.: 1002", .26, .10),
        ("03/07", .19, .16), ("PIX EMIT.OUTRA IF", .26, .16), ("20,00D", .75, .16), ("DOC.: 1003", .26, .18),
        ("03/07", .19, .24), ("SALDO DO DIA", .26, .24), ("330,00C", .75, .24),
        ("RESUMO", .45, .60), ("(+) SALDO EM CONTA:", .19, .64), ("330,00C", .75, .64),
    ]
    if missing_control:
        first = [cell for cell in first if cell[2] != .40]
    return first, second


def test_detached_pending_marker_keeps_release_and_checks_the_idle_day():
    result = pdf_parser.parse_pdf_transactions(grid_pdf(sisbr_pending_cells()))
    assert [t.amount for t in result.transactions] == [50, 200, -20]
    assert result.parse_metrics["non_cash_entries_excluded"] == 1
    assert result.parse_metrics["printed_balance_checks"] == 4
    assert result.parse_metrics["verified_closing_balance"] == 330
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert "pending_deposits_excluded" in result.canonical_transactions[0].warnings
    assert {t.source_page for t in result.canonical_transactions} == {1, 2}


def test_idle_day_divergence_is_checked_even_without_a_cash_movement():
    result = pdf_parser.parse_pdf_transactions(grid_pdf(sisbr_pending_cells(idle_balance="151,00C")))
    assert [t.amount for t in result.transactions] == [50, 200, -20]
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("kwargs", [
    {"marker": ""}, {"marker": "*", "history": "PIX RECEBIDO"}, {"missing_control": True},
])
def test_pending_context_does_not_supply_missing_signs_or_skip_missing_cash_controls(kwargs):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(grid_pdf(sisbr_pending_cells(**kwargs)))


def test_competing_pending_markers_are_not_associated_by_balance_arithmetic():
    first, second = sisbr_pending_cells()
    first += [("C", .83, .475)]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(grid_pdf([first, second]))


def test_pending_marker_outside_the_financial_column_cannot_supply_a_sign():
    first, second = sisbr_pending_cells()
    first = [(text, .5 if text == "*" else x, y) for text, x, y in first]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(grid_pdf([first, second]))


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_an_unreadable_later_month_is_not_silently_dropped(representation):
    first = modern_cells()
    second = [("R$ ?0,00D" if text == "R$ 10,00D" else text, x, y) for text, x, y in modern_cells(month=8, opening=130)]
    with pytest.raises(PdfFinancialValidationError):
        if representation == "native":
            pdf_parser.parse_pdf_transactions(grid_pdf([first, second]))
        else:
            source = extraction(first)
            pdf_parser._parse_scanned_pdf_with_textract_text_pages(
                replace(source, pages=[source.pages[0], extraction(second, page=2).pages[0]]))
