"""Fictional controls for readable ledgers with limited coverage metadata."""
import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from app.application.parsers.pdf.native_table_source import read_native_table_source
from app.application.parsers.pdf.pagbank_native_daily import parse_pagbank_native_daily
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_bradesco_itau_readable_tables import itau_pages
from tests.test_sicoob_channel_pending import parse_pages
from tests.test_sicoob_statement_context import modern_cells


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def pagbank_single_day(*, closing="R$ 4,00", amount="-R$ 20,00"):
    return [[
        ("290 - PAGSEGURO INTERNET S/A", .05, .02),
        ("Extrato da conta", .05, .06),
        ("Agência 0001 Conta 12345-6", .05, .10),
        ("Periodo: 01/08/2026 a 31/08/2026", .05, .14),
        ("Data", .05, .20), ("Descrição", .20, .20), ("Valor", .90, .20),
        ("03/08/2026", .05, .27), ("Pix enviado TESTE", .20, .27), (amount, .88, .27),
        ("03/08/2026", .05, .32), ("Saldo do dia", .20, .32), (closing, .88, .32),
    ]]


def test_pagbank_single_day_preserves_cash_without_claiming_independent_balance_validation():
    result = pdf_parser.parse_pdf_transactions(grid_pdf(pagbank_single_day()))
    assert [(r.date, r.amount) for r in result.transactions] == [("2026-08-03", -20)]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["export_recommendation_reason"] == "opening_inferred_from_closing"
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["printed_balance_checks"] == 0
    assert result.canonical_transactions[0].source_regions


@pytest.mark.parametrize("options", [{"closing": "R$ ?,00"}, {"amount": "-R$ 2?,00"}])
def test_pagbank_single_day_still_rejects_unreadable_financial_fields(options):
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(grid_pdf(pagbank_single_day(**options)))


def test_pagbank_single_day_requires_its_printed_closing_control():
    pages = [[cell for cell in pagbank_single_day()[0] if cell[2] != .32]]
    with pytest.raises(InvalidFileContentError):
        parse_pagbank_native_daily(read_native_table_source(grid_pdf(pages)))


def sicoob_opening_controls(*, blocked=True, mismatch=False):
    cells = modern_cells(expiry=False)
    # Make room after the opening for blocked and available balances dated in
    # the previous month. They are controls, never July cash transactions.
    cells = [(text, x, y + .10 if y > .27 else y) for text, x, y in cells]
    if blocked:
        cells += [("30/06", .05, .30), ("SALDO BLOQUEIO JUDICIAL ANTERIOR", .24, .30), ("R$ 9,00C", .86, .30)]
    cells += [("30/06", .05, .34), ("SALDO DO DIA", .24, .34), ("R$ 101,00C" if mismatch else "R$ 100,00C", .86, .34)]
    return [cells]


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("blocked", [False, True])
def test_sicoob_opening_day_control_uses_opening_role_not_transaction_period(representation, blocked):
    result = parse_pages(sicoob_opening_controls(blocked=blocked), representation)
    assert [r.amount for r in result.transactions] == [50, -10, -10]
    assert [r.date for r in result.transactions] == ["2026-07-01", "2026-07-02", "2026-07-02"]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert all(r.source_regions and r.source_line_refs for r in result.canonical_transactions)


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_sicoob_conflicting_opening_day_control_keeps_movements_for_review(representation):
    result = parse_pages(sicoob_opening_controls(mismatch=True), representation)
    assert [r.amount for r in result.transactions] == [50, -10, -10]
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "financial_validation_failed" in result.parse_metrics["canonical_warning_types"]


def sicoob_without_closing(*, credit=50, intermediate=False, idle=None):
    cells = sicoob_opening_controls(blocked=False)[0]
    daily_positions = [y for text, _, y in cells if text == "SALDO DO DIA" and y > .34]
    removed = daily_positions[-1:] if intermediate else daily_positions
    cells = [(f"R$ {credit},00C" if text == "R$ 50,00C" else text, x, y)
             for text, x, y in cells if y not in removed and y < .75]
    if idle is not None:
        date, balance = ("01/07", "100") if idle == "before" else ("03/07", str(credit + 80))
        if idle == "before":
            cells = [("02/07" if text == "01/07" else "03/07" if text == "02/07" else text, x, y)
                     for text, x, y in cells]
        cells += [(date, .05, .70), ("SALDO DO DIA", .24, .70), (f"R$ {balance},00C", .86, .70)]
    return [cells]


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("credit", [50, 51])
def test_sicoob_opening_comparison_cannot_certify_a_calculated_closing(representation, credit):
    result = parse_pages(sicoob_without_closing(credit=credit), representation)
    assert [r.amount for r in result.transactions] == [credit, -10, -10]
    assert [r.date for r in result.transactions] == ["2026-07-01", "2026-07-02", "2026-07-02"]
    assert result.parse_metrics["printed_balance_checks"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_validation_status"] == "not_available"
    assert result.parse_metrics.get("verified_closing_balance") is None
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["export_recommendation_reason"] == "no_printed_balance_controls"
    assert all(r.source_regions and r.source_line_refs for r in result.canonical_transactions)


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("options", [{"intermediate": True}, {"idle": "before"}])
def test_sicoob_control_before_last_movement_cannot_validate_the_uncontrolled_tail(representation, options):
    result = parse_pages(sicoob_without_closing(**options), representation)
    assert [r.amount for r in result.transactions] == [50, -10, -10]
    assert result.parse_metrics["printed_balance_checks"] == 2
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics.get("verified_closing_balance") is None
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_sicoob_independent_idle_day_after_movements_can_validate_closing(representation):
    result = parse_pages(sicoob_without_closing(idle="after"), representation)
    assert [r.amount for r in result.transactions] == [50, -10, -10]
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert result.parse_metrics["financial_controls_validated"] == 1


def itau_without_summary(*, bad_later=False):
    return [[(text, x, y) for text, x, y in page if not text.startswith("Página")]
            for page in itau_pages(bad_later=bad_later)[:2]]


def test_itau_complete_readable_rows_without_summary_keep_unconfirmed_coverage_review():
    result = pdf_parser.parse_pdf_transactions(grid_pdf(itau_without_summary()))
    assert [r.amount for r in result.transactions] == [20, 20, -5, -15]
    assert {r.source_page for r in result.canonical_transactions} == {1, 2}
    assert all(r.source_regions and r.source_line_refs for r in result.canonical_transactions)
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["partial_statement"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0


def test_itau_missing_summary_does_not_hide_unreadable_later_movements():
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(grid_pdf(itau_without_summary(bad_later=True)))


def test_itau_missing_summary_does_not_allow_unassociated_tail():
    pages = itau_without_summary()
    pages[-1] += [("PAGAMENTO SEM DATA", .18, .70), ("-50,00", .79, .70)]
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(grid_pdf(pages))


def test_itau_repeated_periods_without_summary_keep_both_blocks_and_review():
    pages = itau_without_summary()
    pages += [[(text, x, y) for text, x, y in page if not text.startswith("Página")]
              for page in itau_pages(month="11", opening=120)[:2]]
    result = pdf_parser.parse_pdf_transactions(grid_pdf(pages))
    assert [r.amount for r in result.transactions] == [20, 20, -5, -15] * 2
    assert {r.source_page for r in result.canonical_transactions} == {1, 2, 3, 4}
    assert result.parse_metrics["statement_blocks_count"] == 2
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["financial_controls_validated"] == 0
