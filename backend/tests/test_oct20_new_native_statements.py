import pytest

from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import _parse_pdf_transactions_from_page_texts


def bb_page():
    return "\n".join(
        [
            "Extrato de Conta Corrente",
            "Cliente: EMPRESA EXEMPLO",
            "Período: 01 a 31/07/2026 Agência: 123-4 Conta: 5678-9",
            "Dia Lote Documento Histórico Valor",
            "30/06/2026 Saldo Anterior 1.000,00 (+)",
            "02/07/2026 13105 70201 Pagamento de Boleto 194,57 (-)",
            "EMPRESA EXEMPLO",
            "02/07/2026 13105 Saldo do dia 805,43 (+)",
            "03/07/2026 14020 112101000002018 Cobrança 1.100,00 (+)",
            "03/07/2026 14020 Saldo do dia 1.905,43 (+)",
            "31/07/2026 S A L D O 1.905,43 (+)",
        ]
    )


def xp_page():
    return "\n".join(
        [
            "Conta Digital XP | Extrato",
            "Banco XP S.A | Agência: 0001 | Conta: 123456",
            "De: 01/09/2026 Até: 30/09/2026",
            "Saldo disponível no final do período filtrado: R$ 1.200,00",
            "Data Descrição Valor Saldo",
            "29/09/26 às 10:45:27 Pagamento para EXEMPLO -R$ 300,00 R$ 1.200,00",
            "22/09/26 às 09:12:34 Pix recebido de EXEMPLO R$ 500,00 R$ 1.500,00",
        ]
    )


def banrisul_page():
    return "\n".join(
        [
            "B A N R I S U L - SETEMBRO /2026 - PAG. 1",
            "AGENCIA.0123.45",
            "SALDO ANTERIOR EM 31/08/2026 100,00",
            "DIA OP DOC SIS HISTORICO VALOR SALDO ORIG R*",
            "01 2576 AW4UNW BPX PIX RECEBIDO 200,00",
            "CPF/CNPJ 12345678000190",
            "NOME CLIENTE EXEMPLO",
            "1005 280323 BDX PGTO BOLETO 50,00-",
            "SALDO NA DATA 250,00",
            "02 0449 000000 BDP JUROS 300,00- S",
            "SLD.ENC. 50,00- SALDO NA DATA 50,00-",
            "BDPRVU1-FIM DE EXTRATO 03/10/2026-04:13:15-FOL:000.123",
        ]
    )


@pytest.mark.parametrize(
    "page,amounts,closing",
    [
        (bb_page, [-194.57, 1100], 1905.43),
        (xp_page, [500, -300], 1200),
        (banrisul_page, [200, -50, -300], -50),
    ],
)
def test_new_native_variants_preserve_amount_roles_and_ledger(page, amounts, closing):
    result = _parse_pdf_transactions_from_page_texts([page()])
    assert [t.amount for t in result.transactions] == amounts
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["verified_closing_balance"] == closing
    assert all(row.source_page == 1 for row in result.canonical_transactions)


def test_bb_keeps_counterparty_on_continuation_lines():
    result = _parse_pdf_transactions_from_page_texts([bb_page()])
    assert "EMPRESA EXEMPLO" in result.transactions[0].description


@pytest.mark.parametrize(
    "page,before,after",
    [
        (bb_page, "805,43 (+)", "804,43 (+)"),
        (xp_page, "-R$ 300,00", "-R$ 301,00"),
        (banrisul_page, "250,00", "251,00"),
    ],
)
def test_bad_printed_control_cannot_fall_through_to_generic_parser(page, before, after):
    with pytest.raises(PdfFinancialValidationError):
        _parse_pdf_transactions_from_page_texts([page().replace(before, after)])


def test_unbranded_export_without_signs_cannot_turn_payments_into_credits():
    page = "02/06/25\n9079938\nLIQUIDACAO DE COBRANCA VALOR DISPONIVEL\n500,00\n"
    page += "02/06/25\n0001801\nPAGTO ELETRON COBRANCA EXEMPLO\n100,00\n"
    page += "02/06/25\n8140494\nAPLIC. INVEST FACIL\n400,00"
    with pytest.raises(PdfFinancialValidationError, match="sinal|débito|crédito"):
        _parse_pdf_transactions_from_page_texts([page])
