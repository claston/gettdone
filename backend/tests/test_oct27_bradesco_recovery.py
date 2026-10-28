"""Fictional reproductions of Bradesco header and undated-opening failures."""
import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.recovery_acceptance import grid_pdf


def bradesco_continuation(*, undated_opening=False, mutation=None, scale=1):
    header = [("Extrato Mensal / Por Período Bradesco net empresa", .06, .03),
              ("Extrato de: Ag: 1234 | CC: 0012345-6 | Entre 01/12/2026 e 31/12/2026", .06, .08),
              ("Data", .06, .14), ("Lançamento", .18, .14), ("Dcto.", .49, .14),
              ("Crédito (R$)", .62, .14), ("Débito (R$)", .73, .14), ("Saldo (R$)", .88, .14)]
    opening = [("SALDO ANTERIOR", .18, .20), ("-100,00", .88, .20)]
    if not undated_opening:
        opening.append(("30/11/2026", .06, .20))
    pages = [header + opening + [
        ("01/12/2026", .06, .28), ("RECEBIMENTO CARTÃO", .18, .275),
        ("EMPRESA FICTÍCIA", .18, .295), ("1001", .49, .28), ("500,00", .64, .28), ("400,00", .88, .28),
        ("Folha 1/2", .88, .10)], [
        ("Extrato Mensal / Por Período", .06, .02), ("CNPJ:", .42, .05), ("027", .50, .05),
        ("Folha 2/2", .88, .10),
        ("PAGTO ELETRON COBRANCA", .18, .195), ("EMPRESA TESTE", .18, .215),
        ("1002", .49, .20), ("-150,00", .75, .20), ("250,00", .88, .20),
        ("02/12/2026", .06, .30), ("TRANSFERÊNCIA PIX", .18, .295), ("DESTINO TESTE", .18, .315),
        ("1003", .49, .30), ("-150,00", .75, .30), ("100,00", .88, .30),
        ("Total", .18, .40), ("500,00", .64, .40), ("300,00", .75, .40), ("100,00", .88, .40),
        ("Saldos Invest Fácil / Plus", .06, .55), ("03/12/2026", .06, .65),
        ("SALDO INVEST FÁCIL", .18, .65), ("999,00", .88, .65)]]
    replacements = {
        "out_of_period": {"02/12/2026": "02/01/2027"},
        "wrong_total": {"300,00": "301,00"},
        "signed_debit_total": {"300,00": "-300,00"},
        "invalid_date": {"02/12/2026": "32/12/2026"},
        "invalid_period": {"Entre 01/12/2026 e 31/12/2026": "Entre 31/12/2026 e 01/12/2026"},
        "orphan_reference": {},
    }
    if mutation in replacements:
        pages = [[(next((text.replace(a, b) for a, b in replacements[mutation].items() if a in text), text), x, y)
                  for text, x, y in page] for page in pages]
    if mutation == "orphan_reference":
        pages[1].append(("999999", .49, .25))
    if mutation == "missing_value":
        pages[0] = [cell for cell in pages[0] if cell[0] != "500,00"]
    if mutation == "missing_period":
        pages[0] = [(text.split(" | Entre ")[0] if text.startswith("Extrato de:") else text, x, y) for text, x, y in pages[0]]
    return grid_pdf(pages, scale)


@pytest.mark.parametrize("scale", [.75, 1, 1.5])
@pytest.mark.parametrize("undated_opening", [False, True])
def test_bradesco_continuation_header_is_not_a_transaction_reference(scale, undated_opening, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    result = pdf_parser.parse_pdf_transactions(bradesco_continuation(scale=scale, undated_opening=undated_opening))
    assert [(row.date, row.amount) for row in result.transactions] == [
        ("2026-12-01", 500), ("2026-12-01", -150), ("2026-12-02", -150)]
    assert [row.external_reference_id for row in result.canonical_transactions] == ["1001", "1002", "1003"]
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == 100
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert not any(row.warnings for row in result.canonical_transactions)
    assert all(row.source_regions for row in result.canonical_transactions)
    assert all("027" not in row.description and "INVEST FÁCIL" not in row.description for row in result.transactions)


@pytest.mark.parametrize("mutation", ["out_of_period", "wrong_total"])
def test_bradesco_printed_inconsistencies_remain_readable_with_financial_warning(mutation):
    result = pdf_parser.parse_pdf_transactions(bradesco_continuation(undated_opening=True, mutation=mutation))
    assert [row.amount for row in result.transactions] == [500, -150, -150]
    if mutation == "out_of_period":
        assert result.transactions[-1].date == "2027-01-02"
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings
    assert result.parse_metrics["financial_controls_validated"] == 0


@pytest.mark.parametrize("mutation", ["orphan_reference", "missing_value", "invalid_date", "invalid_period"])
def test_bradesco_still_rejects_ambiguous_or_missing_ledger_evidence(mutation):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(bradesco_continuation(undated_opening=True, mutation=mutation))


def test_bradesco_missing_period_converts_fully_dated_movements_for_review():
    result = pdf_parser.parse_pdf_transactions(bradesco_continuation(undated_opening=True, mutation="missing_period"))
    assert [row.amount for row in result.transactions] == [500, -150, -150]
    assert result.parse_metrics["document_period_unavailable"] == 1
    assert result.parse_metrics["statement_validation_failed"] == 0


def test_bradesco_undated_opening_and_bank_metadata_reach_the_shared_pipeline():
    analysis = build_default_conversion_pipeline().run(filename="fictional.pdf",
        raw_bytes=bradesco_continuation(undated_opening=True), analysis_id="an_bradesco_fictional").analysis_data
    assert analysis.transactions_total == 3
    assert analysis.opening_balance == -100
    assert analysis.closing_balance == 100
    assert analysis.bank_code == "237"


def test_bradesco_negative_debit_total_is_a_column_magnitude_not_a_false_difference():
    result = pdf_parser.parse_pdf_transactions(bradesco_continuation(undated_opening=True, mutation="signed_debit_total"))
    assert [row.amount for row in result.transactions] == [500, -150, -150]
    assert not any(row.warnings for row in result.canonical_transactions)
