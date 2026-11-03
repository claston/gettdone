"""Fictional account-detail PDFs preserve columns independently of issuer names."""
import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.parsers.pdf.native_table_source import read_native_table_source
from tests.fixtures.recovery_acceptance import grid_pdf


def account_detail_pdf(*, owner="Nome", joined=True, mutation=None):
    history_x = .105 if joined else .20
    header = [("DETALHE DOS MOVIMENTOS", .06, .26), ("Data", .06, .30),
              ("Descrição", history_x, .30), ("Valor", .72, .30), ("Saldo", .86, .30)]
    summary = [("EXTRATO DE CONTA", .65, .03), ("Agência: 0001 | Conta: 12345-6", .06, .06),
               ("Período: 01/10/2026 a 31/10/2026", .06, .09),
               ("Saldo inicial", .08, .18), ("Entradas", .30, .18), ("Saídas", .52, .18), ("Saldo final", .74, .18),
               ("R$ 100,00", .08, .21), ("R$ 50,12", .30, .21), ("R$ 40,00", .52, .21), ("R$ 110,12", .74, .21)]
    if owner:
        summary.append((f"{owner}: CLIENTE FICTÍCIO", .06, .12))

    def row(date, y, description, amount, balance, continuation=None):
        result = [(date, .06, y), (description, history_x, y), (amount, .71, y), (balance, .85, y)]
        if continuation:
            result.append((continuation, history_x, y + .014))
        return result

    pages = [summary + header
             + row("03/10/2026", .40, "Remuneração aplicação automática", "R$ 0,12", "R$ 110,12")
             + row("02/10/2026", .48, "Tarifa mensalidade", "-R$ 10,00", "R$ 110,00", "Setembro / 2026")
             + row("02/10/2026", .56, "Pix enviado", "-R$ 10,00", "R$ 120,00", "Caixa Economica Feder")
             + [("1/2", .91, .97)],
             header + row("02/10/2026", .40, "Pix enviado", "-R$ 20,00", "R$ 130,00", "Banco Santander (br)")
             + row("01/10/2026", .48, "Pix recebido", "R$ 50,00", "R$ 150,00") + [("2/2", .91, .97)]]
    replacements = {
        "bad_date": {"03/10/2026": "33/10/2026"},
        "balance_mismatch": {"R$ 130,00": "R$ 131,00"},
        "summary_mismatch": {"R$ 40,00": "R$ 41,00"},
    }
    if mutation in replacements:
        pages = [[(replacements[mutation].get(text, text), x, y) for text, x, y in page] for page in pages]
    if mutation == "missing_amount":
        pages[1] = [cell for cell in pages[1] if cell[0] != "-R$ 20,00"]
    if mutation == "orphan_amount":
        pages[1].append(("R$ 9,00", .71, .70))
    return grid_pdf(pages)


@pytest.mark.parametrize("owner", ["Nome", "Titular", None])
@pytest.mark.parametrize("joined", [False, True])
def test_owner_label_and_native_fragment_spacing_do_not_change_movements(owner, joined, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    raw = account_detail_pdf(owner=owner, joined=joined)
    if joined:
        # Exercise a real merged native LINE; date/history WORD coordinates remain distinct.
        source = read_native_table_source(raw)
        assert any(line.text.startswith("03/10/2026 Remuneração") for line in source.pages[0].lines)
    result = pdf_parser.parse_pdf_transactions(raw)
    assert result.layout.layout_name == "account_detail_descending_cash_v1"
    assert [(row.date, row.amount) for row in result.transactions] == [
        ("2026-10-03", .12), ("2026-10-02", -10), ("2026-10-02", -10),
        ("2026-10-02", -20), ("2026-10-01", 50)]
    assert result.transactions[1].description == "Tarifa mensalidade Setembro / 2026"
    assert [row.source_page for row in result.canonical_transactions] == [1, 1, 1, 2, 2]
    assert all(row.source_regions for row in result.canonical_transactions)
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 110.12
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["textract_attempted"] == 0


@pytest.mark.parametrize("mutation", ["bad_date", "missing_amount", "orphan_amount"])
def test_joined_native_columns_do_not_hide_unreadable_or_unpaired_fields(mutation):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(account_detail_pdf(mutation=mutation))


@pytest.mark.parametrize("mutation", ["balance_mismatch", "summary_mismatch"])
def test_joined_native_columns_preserve_real_control_mismatches_for_review(mutation):
    result = pdf_parser.parse_pdf_transactions(account_detail_pdf(mutation=mutation))
    assert len(result.transactions) == 5
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings
    assert result.parse_metrics["financial_controls_validated"] == 0


def test_pipeline_does_not_identify_issuer_from_payment_recipients():
    data = build_default_conversion_pipeline().run(filename="account.pdf", raw_bytes=account_detail_pdf(),
        analysis_id="an_account_detail").analysis_data
    assert data.bank_name is None
    assert (data.opening_balance, data.closing_balance, data.net_total) == (100, 110.12, 10.12)


def test_large_amount_and_balance_in_one_native_fragment_keep_separate_column_roles():
    raw = grid_pdf([[
        ("EXTRATO DE CONTA", .65, .03), ("Nome: CLIENTE FICTÍCIO", .06, .09),
        ("Período: 01/10/2026 a 31/10/2026", .06, .12),
        ("Saldo inicial", .08, .18), ("Entradas", .30, .18), ("Saídas", .52, .18), ("Saldo final", .74, .18),
        ("R$ 2.000.000,00", .08, .21), ("R$ 0,00", .30, .21),
        ("R$ 1.000.000,00", .52, .21), ("R$ 1.000.000,00", .74, .21),
        ("DETALHE DOS MOVIMENTOS", .06, .26), ("Data", .06, .30),
        ("Descrição", .20, .30), ("Valor", .72, .30), ("Saldo", .78, .30),
        ("01/10/2026", .06, .40), ("Pix enviado", .20, .40),
        ("-R$ 1.000.000,00", .71, .40), ("R$ 1.000.000,00", .78, .40), ("1/1", .91, .97),
    ]])
    source = read_native_table_source(raw)
    assert any(line.text.count("R$") == 2 for line in source.pages[0].lines)
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [(row.amount, row.running_balance) for row in result.canonical_transactions] == [(-1000000, 1000000)]
    assert result.parse_metrics["financial_controls_validated"] == 1
