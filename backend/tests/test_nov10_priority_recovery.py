"""Fictional regressions for row roles, Wise metadata and signed C6 controls."""
import re

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_bradesco_itau_readable_tables import itau_pages
from tests.test_sicoob_channel_pending import parse_pages
from tests.test_wise_statements import wise_lines


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def bb_paired_dates_cells(*, amount="20,00 D", second_date="30/09/2026"):
    return [
        ("Cliente Conta atual Dt. balancete Dt. movimento Ag. origem", .05, .02),
        ("Agência 0001 Conta corrente 12345-6", .05, .05),
        ("Período do extrato 10 / 2026", .05, .07),
        ("Histórico", .35, .1), ("Documento", .65, .1), ("Valor R$", .75, .1), ("Saldo", .86, .1),
        ("30/09/2026", .08, .20), ("000 Saldo Anterior", .35, .20), ("100,00 C", .86, .20),
        (f"01/10/2026 {second_date}", .08, .22), ("109 Pagamento de Boleto", .35, .22),
        ("1001", .65, .22), (amount, .75, .22),
        ("01/10/2026", .08, .26), ("821 Pix Recebido", .35, .26),
        ("1002", .65, .26), ("20,00 C", .75, .26), ("100,00 C", .86, .26),
        ("31/10/2026", .08, .32), ("999 Saldo", .35, .32), ("100,00 C", .86, .32),
    ]


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_bb_joined_book_and_movement_dates_do_not_merge_debit_into_opening(representation):
    result = parse_pages([bb_paired_dates_cells()], representation)
    assert [row.amount for row in result.transactions] == [-20, 20]
    assert [row.date for row in result.transactions] == ["2026-09-30", "2026-10-01"]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 100
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("amount", ["2?,00 D", "20,00"])
def test_bb_paired_dates_do_not_repair_unreadable_amount_or_sign(representation, amount):
    with pytest.raises(InvalidFileContentError):
        parse_pages([bb_paired_dates_cells(amount=amount)], representation)


def itau_same_page_summary(*, bad_amount=False):
    pages = itau_pages(ascending=False, bad_later=bad_amount)
    # The summary has its own financial column positions below the ledger.
    summary = [("Saldo da conta corrente", .05, .65),
               ("Descrição", .05, .70), ("Valor (R$)", .87, .70), ("Saldo (R$)", .92, .70),
               ("SALDO TOTAL DISPONÍVEL", .05, .75), ("120,00", .92, .75)]
    # One complete table on a page, with the summary in the same physical page.
    first = [(text, x, y) for text, x, y in pages[0] if not text.startswith("Página")]
    continuation = [(text, x, y+.26) for text, x, y in pages[1] if not text.startswith("Página")]
    return [first + continuation + summary]


def test_itau_summary_header_does_not_move_transaction_amounts_into_tax_id_column():
    result = pdf_parser.parse_pdf_transactions(grid_pdf(itau_same_page_summary()))
    assert [row.amount for row in result.transactions] == [-15, -5, 20, 20]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 120
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)


def test_itau_summary_cannot_supply_an_unreadable_movement_amount():
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(grid_pdf(itau_same_page_summary(bad_amount=True)))


def wise_variant(*, currency="BRL", date_first=False, pipes=True, suffix=True):
    lines = wise_lines(currency)
    result = lines[:11]
    for index in range(11, len(lines)-2, 2):
        financial, metadata = lines[index:index+2]
        if suffix:
            metadata += " | Referência: Fatura: teste 123"
        if not pipes:
            metadata = metadata.replace(" | ", " ")
        if date_first:
            match = re.fullmatch(r"(.+) ([+-]?[\d.]+,\d{2}) ([\d.]+,\d{2})", financial)
            result += [match[1], metadata, f"{match[2]} {match[3]}"]
        else:
            result += [financial, metadata]
    return result + lines[-2:]


@pytest.mark.parametrize("date_first,pipes,suffix", [(False, True, True), (True, False, True), (True, True, False)])
@pytest.mark.parametrize("currency", ["BRL", "USD"])
def test_wise_variants_keep_actual_transaction_ids_dates_and_money(date_first, pipes, suffix, currency):
    lines = wise_variant(currency=currency, date_first=date_first, pipes=pipes, suffix=suffix)
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(lines),)))
    assert [row.amount for row in result.transactions] == [40, -40, 900, 450, 30, -1378.5]
    assert [row.date for row in result.transactions] == ["2026-04-07"]*2 + ["2026-04-10"]*4
    assert [row.external_reference_id for row in result.canonical_transactions] == [
        "TRANSFER-100001", "BANK_DETAILS_ORDER_CHECKOUT-invoice-100002",
        "TRANSFER-100003", "TRANSFER-100004", "TRANSFER-100005", "TRANSFER-100006",
    ]
    assert [row.running_balance for row in result.canonical_transactions] == [40, 0, 900, 1350, 1380, 1.5]
    assert all(row.source_line_refs for row in result.canonical_transactions)
    assert all("Fatura" not in row.description for row in result.transactions)


@pytest.mark.parametrize("mutation", ["duplicate_id", "missing_money", "missing_date", "extra_money", "invalid_date"])
def test_wise_metadata_variants_do_not_hide_missing_or_ambiguous_rows(mutation):
    lines = wise_variant(date_first=True, pipes=False)
    if mutation == "duplicate_id":
        lines = [line.replace("TRANSFER-100005", "TRANSFER-100006") for line in lines]
    elif mutation == "missing_money":
        lines.remove("-1.378,50 1,50")
    elif mutation == "missing_date":
        lines.pop(12)
    elif mutation == "invalid_date":
        lines = [line.replace("10 de abril", "31 de abril") for line in lines]
    else:
        lines.insert(14, "99,00 100,50")
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(lines),)))


def test_wise_date_and_money_can_continue_on_next_physical_page():
    lines = wise_variant(date_first=True, pipes=False)
    first = [line.replace("1 / 1", "1 / 2") for line in lines[:13]]
    second = ["ref:synthetic-wise 2 / 2", "Descrição Entrada Saída Valor", *lines[13:]]
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(first), tuple(second))))
    assert len(result.transactions) == 6
    assert result.transactions[-1].amount == -1378.5
    assert {page for page, _ in result.canonical_transactions[-1].source_line_refs} == {1, 2}


def test_wise_shared_customer_reference_does_not_replace_distinct_transaction_ids():
    lines = wise_variant()
    lines[11:11] = [
        "Ajuste 0,00 1,50", "10 de abril de 2026|Transação: TRANSFER-100008 | Referência: Fatura: teste 123",
        "Ajuste 0,00 1,50", "10 de abril de 2026|Transação: TRANSFER-100007 | Referência: Fatura: teste 123",
    ]
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(lines),)))
    assert len(result.transactions) == 8
    assert [row.external_reference_id for row in result.canonical_transactions[-2:]] == ["TRANSFER-100007", "TRANSFER-100008"]


@pytest.mark.parametrize("date_first", [True, False])
def test_wise_readable_variant_keeps_balance_disagreement_in_review(date_first):
    lines = [line.replace("30,00 1.380,00", "30,00 1.381,00") for line in wise_variant(date_first=date_first)]
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(lines),)))
    assert len(result.transactions) == 6
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["financial_controls_validated"] == 0


def c6_negative_balance(balance="-R$ 20,00", *, inline=False):
    from tests.test_audited_financial_extractions import C6

    text = C6.replace("R$ 20,00", "R$ 120,00").replace("R$ 80,00", balance).replace("R$ 120,00\nInformações", "R$ 20,00\nInformações")
    if inline:
        text = text.replace("Saldo do dia 30/04/26\n"+balance, "Saldo do dia 30/04/26 "+balance)
    return text


@pytest.mark.parametrize("balance", ["-R$ 20,00", "- R$ 20,00", "R$ -20,00"])
@pytest.mark.parametrize("inline", [True, False])
def test_c6_negative_balance_preserves_sign_and_monthly_transactions(balance, inline):
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(c6_negative_balance(balance, inline=inline).splitlines()),)))
    assert [row.amount for row in result.transactions] == [100, -120, 50, -10]
    assert [row.date for row in result.transactions] == ["2026-04-30"]*2 + ["2026-05-11"]*2
    assert not result.parse_metrics.get("statement_validation_failed")
    assert result.parse_metrics["printed_balance_checks"] >= 5
    assert all(row.source_line_refs for row in result.canonical_transactions)


@pytest.mark.parametrize("balance", ["--R$ 20,00", "-R$ -20,00", "-R$ 2?,00"])
@pytest.mark.parametrize("inline", [True, False])
def test_c6_invalid_balance_signs_and_digits_never_become_a_valid_control(balance, inline):
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(c6_negative_balance(balance, inline=inline).splitlines()),)))


def test_c6_readable_negative_balance_does_not_hide_an_arithmetic_disagreement():
    text = c6_negative_balance("-R$ 21,00")
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(text.splitlines()),)))
    assert [row.amount for row in result.transactions] == [100, -120, 50, -10]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["financial_controls_validated"] == 0
