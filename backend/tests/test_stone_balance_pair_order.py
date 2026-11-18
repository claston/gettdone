"""Fictional Stone ledger pairs, including repeated sales and a page break."""

from dataclasses import asdict, replace

import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.models import CanonicalTransaction
from app.application.normalization.balance import annotate_balance_consistency
from app.application.pdf_review_guidance import build_pdf_review_guidance
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

LAYOUT = "stone_extrato_conta_corrente_a4_v1"


def ledger(credit_first):
    def row(day, description, amount, balance):
        return CanonicalTransaction(date=day, description=description, amount=amount,
                                    type="inflow" if amount > 0 else "outflow", running_balance=balance, layout_name=LAYOUT)

    def pair(day, amount, fee, balance):
        credit = row(day, "Entrada Pix Maquininha CLIENTE FICTICIO", amount, balance)
        debit = row(day, "Entrada Cobranca de Tarifa", -fee, balance)
        return [credit, debit] if credit_first else [debit, credit]

    rows = [*pair("2026-02-11", 20, .5, 139), *pair("2026-02-11", 20, .5, 119.5),
            row("2026-02-10", "Saida Transferencia Pix", -80, 100), *pair("2026-02-10", 30, 1, 180)]
    return [replace(row, source_page=1 if index < 3 else 2, source_line=index+1, source_sequence=index+1)
            for index, row in enumerate(rows)]


def statement_pages(credit_first):
    header = ["Extrato de Conta Corrente", "Stone Instituicao de Pagamento S.A.",
              "Periodo: 01/02/26 a 28/02/26", "DATA TIPO DESCRICAO VALOR SALDO CONTRAPARTE"]

    def printed(row):
        year, month, day = row.date.split("-")
        # Newer statements label fees as Entrada and use a decimal dot in the fee cell.
        amount = f"-R$ {abs(row.amount):.2f}" if row.amount < 0 else "R$ " + f"{row.amount:.2f}".replace(".", ",")
        balance = f"{row.running_balance:.2f}".replace(".", ",")
        return f"{day}/{month}/{year[2:]} {row.description} {amount} R$ {balance}"

    return tuple(tuple(header + [printed(row) for row in ledger(credit_first) if row.source_page == page]) for page in (1, 2))


@pytest.mark.parametrize("credit_first", [False, True])
def test_balance_pairs_keep_both_orders_values_repeats_and_provenance(credit_first):
    rows = ledger(credit_first)
    original = [asdict(row) for row in rows]
    assert annotate_balance_consistency(rows) == (3, 0)
    assert [asdict(row) for row in rows] == original


@pytest.mark.parametrize("credit_first", [False, True])
def test_pair_total_still_exposes_real_discrepancy(credit_first):
    rows = ledger(credit_first)
    rows[4].running_balance = 99
    checked, failed = annotate_balance_consistency(rows)
    assert checked == 3
    assert failed > 0
    check = rows[4].balance_check
    assert check.reference_balance == 119.5
    assert check.expected_balance == 100
    assert check.reported_balance == 99
    assert check.difference == -1
    assert check.movements_count == 2
    assert rows[4].warnings == ["balance_consistency_failed"]


@pytest.mark.parametrize("credit_first", [False, True])
def test_a_pair_does_not_absorb_an_extra_row_with_the_same_balance(credit_first):
    rows = ledger(credit_first)[:2]
    rows.append(replace(rows[1], source_line=3, source_sequence=3))
    _, failed = annotate_balance_consistency(rows)
    assert failed > 0
    assert rows[2].warnings == ["balance_consistency_failed"]


@pytest.mark.parametrize("credit_first", [False, True])
def test_pair_members_must_be_adjacent(credit_first):
    rows = ledger(credit_first)[:2]
    rows.insert(1, replace(rows[0], description="Saida Pix", amount=-5, type="outflow", running_balance=None))
    assert annotate_balance_consistency(rows)[1] > 0
    assert rows[2].warnings == ["balance_consistency_failed"]


@pytest.mark.parametrize("change", [
    {"description": "Entrada Estorno"},
    {"date": "2026-02-10"},
    {"running_balance": 138},
    {"layout_name": "generic_statement_ptbr"},
])
def test_only_a_matching_fee_can_share_the_credit_balance(change):
    rows = ledger(True)[:2]
    rows[1] = replace(rows[1], **change)
    assert annotate_balance_consistency(rows)[1] > 0
    assert rows[1].warnings == ["balance_consistency_failed"]


@pytest.mark.parametrize("credit_first", [False, True])
@pytest.mark.parametrize("representation", ["text", "native"])
def test_shared_parser_preserves_both_pair_orders_across_pages(monkeypatch, credit_first, representation):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    rows = ledger(credit_first)
    pages = statement_pages(credit_first)
    if representation == "text":
        result = pdf_parser._parse_pdf_transactions_from_page_texts(["\n".join(page) for page in pages])
        actual = result.canonical_transactions
        metrics = result.parse_metrics
    else:
        raw = generate_native_text_pdf(tuple(tuple(page) for page in pages))
        analysis = build_default_conversion_pipeline().run(filename="stone-fictional.pdf", raw_bytes=raw,
                                                           analysis_id="an_stone_pairs").analysis_data
        actual = analysis.preview_transactions
        metrics = analysis.pdf_processing_metrics
        assert analysis.transactions_total == 7
        assert analysis.total_inflows == 70
        assert analysis.total_outflows == -82
        assert analysis.closing_balance == 139
        assert build_pdf_review_guidance(analysis)["category"] != "financial_discrepancy"
    assert [(row.date, row.amount, row.running_balance, row.source_page) for row in actual] == [
        (row.date, row.amount, row.running_balance, row.source_page) for row in rows]
    assert metrics["balance_consistency_checked"] == 3
    assert metrics["balance_consistency_failed"] == 0
