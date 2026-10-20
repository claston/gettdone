import pytest

from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import _parse_pdf_transactions_from_page_texts


def portal_pages():
    return [
        "\n".join([
            "Banco do Estado do Rio Grande do Sul S.A.", "BVP-PORTAL BANRISUL Página: 1",
            "EXTRATO MESES ANTERIORES - (4F37)", "PERIODO: 08/2026",
            "OPERACAO DOCUMENTO V A L O R S A L D O SIST. AG.ORIGEM",
            "MOVIMENTOS DA CONTA CORRENTE", "SALDO ANT EM 31/07/2026 100,00-",
            "03/08/2026 0672 PAGAMENTO FORNECEDOR 000001 150,00 BRR",
            "CPF/CNPJ: 00000000000000", "1005 PGTO BOLETO 000002 20,00- BDX",
            "SALDO NA DATA 30,00", "U=NC10$ T=000000 I=PDF G=30 V=1",
        ]),
        "\n".join([
            "Banco do Estado do Rio Grande do Sul S.A.", "BVP-PORTAL BANRISUL Página: 2",
            "EXTRATO MESES ANTERIORES - (4F37)", "PERIODO: 08/2026",
            "1005 PGTO BOLETO 000003 50,00- BDX", "SALDO NA DATA 20,00-",
            "SALDO CALC. ENCARGO 10,00-", "U=NC10$ T=000000 I=PDF G=30 V=1",
        ]),
    ]


def mister_page():
    return "\n".join([
        "Relatório de Conta Corrente", "Banco: Santander Período: julho/2026",
        "Lançamento Histórico Débito Crédito", "01/07/2026 Aplicacao Contamax -1,100.00",
        "01/07/2026 Pix Recebido 00000000000000 1,200.50",
        "02/07/2026 Pix Enviado EXEMPLO -100.50",
    ])


def test_portal_banrisul_keeps_dateless_rows_negative_opening_and_page_continuation():
    result = _parse_pdf_transactions_from_page_texts(portal_pages())
    assert [r.amount for r in result.transactions] == [150, -20, -50]
    assert [r.date for r in result.transactions] == ["2026-08-03"] * 3
    assert [r.external_reference_id for r in result.canonical_transactions] == ["000001", "000002", "000003"]
    assert result.canonical_transactions[-1].source_page == 2
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == -20
    assert result.parse_metrics["printed_balance_checks"] == 2
    assert result.parse_metrics["statement_row_coverage_rate"] == 1


@pytest.mark.parametrize("before,after", [("50,00- BDX", "51,00- BDX"), ("150,00 BRR", "150,0 BRR")])
def test_portal_banrisul_never_falls_through_after_a_missing_or_bad_financial_row(before, after):
    with pytest.raises(PdfFinancialValidationError):
        _parse_pdf_transactions_from_page_texts([p.replace(before, after) for p in portal_pages()])


def test_mister_contador_explicit_columns_us_amounts_and_no_invented_balances():
    result = _parse_pdf_transactions_from_page_texts([mister_page()])
    assert [r.amount for r in result.transactions] == [-1100, 1200.5, -100.5]
    assert all(r.running_balance is None for r in result.canonical_transactions)
    assert result.layout.layout_name == "santander_mister_contador_signed_report_v1"
    assert result.parse_metrics["balance_validation_status"] == "not_available"
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["statement_row_coverage_rate"] == 1


def test_mister_contador_unknown_direction_is_rejected():
    with pytest.raises(PdfFinancialValidationError):
        _parse_pdf_transactions_from_page_texts([mister_page().replace("-100.50", "100.50")])
