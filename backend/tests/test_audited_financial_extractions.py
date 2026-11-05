"""Fictional regressions for the seven November audit documents."""

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import grid_pdf


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


SAFRA = """Banco Safra S/A
Página 1 de 1
05/11/2026 09:36
Extrato de Conta Corrente
Ag: 0001 Conta: 12345-6
Período de 01/03/2025 a 31/03/2025
LANÇAMENTOS REALIZADOS
Data Lançamento Complemento Nº Documento Valor (R$)
05/03 RESUMO VENDAS CARTAO DEB REDE ELO DEBITO 001 100,00
05/03 TARIFA PIX ENVIADO 002 -10,00
05/03 SALDO TOTAL 190,00
06/03 RESUMO VENDAS CARTAO DEB REDE ELO DEBITO 003 100,00
06/03 SALDO TOTAL 290,00
"""

C6 = """C6 BANK
Extrato exportado no dia 03 de junho de 2026
Agência: 1 Conta: 12345
Período 30 de abril de 2026 até 30 de maio de 2026
Abril 2026 (30/04/2026 30/04/2026)
Entradas: R$ 100,00
Saídas: R$ 20,00
Data Data lançamento contábil Tipo Descrição Valor
30/04
30/04
Entradas
RESGATE DE CDB
R$ 100,00
30/04
30/04
Pagamento
TRIBUTO
-R$ 20,00
Saldo do dia 30/04/26
R$ 80,00
Maio 2026 (01/05/2026 30/05/2026)
Entradas: R$ 50,00
Saídas: R$ 10,00
Data Data lançamento contábil Tipo Descrição Valor
11/05
11/05
Entrada PIX
Pix recebido TESTE
R$ 50,00
11/05
11/05
Pagamento
TRIBUTO
-R$ 10,00
Saldo do dia 11/05/26
R$ 120,00
Informações sujeitas a alteração
"""

PORTFOLIO = """RELATÓRIO
PORTFÓLIO DE INVESTIMENTOS
Banco do Brasil
bb.com.br/investimentos
Período: 30/09/2025 até 30/09/2026
Posição da carteira
CLASSE DO ATIVO SALDO BRUTO ENTRADAS SAÍDAS SALDO BRUTO PROVISÃO IR/IOF SALDO LÍQUIDO RENTABILIDADE (%)
RENDA FIXA PÓS CDI R$ 100,00 R$ 50,00 R$ 20,00 R$ 140,00 R$ 2,00 R$ 138,00 10,00%
TOTAL R$ 100,00 R$ 50,00 R$ 20,00 R$ 140,00 R$ 2,00 R$ 138,00 10,00%
"""

COLLECTION = """SISBR - Cobrança Bancária 3.0
Data de Emissão: 05/11/2026
Relatório de Lançamentos em Conta - Analítico
0001 - SICOOB TESTE
Período Lançamento: 01/10/2026 a 31/10/2026
Situação: Sucesso
Histórico: Crédito
Contrato Pagador CPF/CNPJ Nosso Número Seu Número Vencimento Pagamento Lançamento Float Valor Nominal Valor Pago
123 CLIENTE FICTICIO 00.000.000/0001-00 111 DOC-A 01/10/2026 13/10/2026 14/10/2026 1 R$ 100,00 R$ 95,00
123 CLIENTE FICTICIO 00.000.000/0001-00 112 DOC-B 01/10/2026 13/10/2026 14/10/2026 1 R$ 100,00 R$ 95,00
Total R$ 200,00 R$ 190,00
"""


@pytest.mark.parametrize("native", [False, True])
def test_period_year_and_signed_column_override_emission_and_description(native):
    result = (
        pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(SAFRA.splitlines()),)))
        if native
        else pdf_parser._parse_pdf_transactions_from_page_texts([SAFRA])
    )
    assert [(t.date, t.amount) for t in result.transactions] == [("2025-03-05", 100), ("2025-03-05", -10), ("2025-03-06", 100)]
    assert all(t.source_page == 1 for t in result.canonical_transactions)


def test_c6_monthly_summary_is_not_a_transaction_in_previous_month():
    result = pdf_parser._parse_pdf_transactions_from_page_texts([C6])
    assert [(t.date, t.amount) for t in result.transactions] == [
        ("2026-04-30", 100),
        ("2026-04-30", -20),
        ("2026-05-11", 50),
        ("2026-05-11", -10),
    ]


def test_stone_currency_sign_wins_over_entry_type_and_keeps_decimal_dot():
    page = """Extrato de Conta Corrente
stone
Período: 01/10/26 a 31/10/26
DATA TIPO DESCRIÇÃO VALOR SALDO CONTRAPARTE
31/10/26 Entrada Pix Maquininha R$ 17,00 R$ 116,88
31/10/26 Entrada Cobrança de Tarifa -R$ 0.12 R$ 116,88
"""
    result = pdf_parser._parse_pdf_transactions_from_page_texts([page])
    assert [t.amount for t in result.transactions] == [17, -0.12]


def test_portfolio_positions_and_percentages_never_become_cash():
    result = pdf_parser._parse_pdf_transactions_from_page_texts([PORTFOLIO])
    assert result.transactions == []
    assert result.financial_document["document_type"] == "investment_statement"
    assert result.financial_document["observations"]
    assert result.financial_document["export"]["available_formats"] == ["csv", "xlsx"]


def test_analytic_collections_keep_all_three_dates_and_paid_column():
    result = pdf_parser._parse_pdf_transactions_from_page_texts([COLLECTION])
    assert [(t.date, t.amount) for t in result.transactions] == [("2026-10-14", 95)] * 2
    assert all(t.running_balance is None for t in result.canonical_transactions)
    assert result.financial_document["events"][0]["dates"] == {"due": "2026-10-01", "payment": "2026-10-13", "transaction": "2026-10-14"}


@pytest.mark.parametrize("text", [SAFRA.replace("31/03/2025", "31/03/2024"), C6.replace("-R$ 10,00", "-R$ ??,00")])
def test_ambiguous_financial_fields_are_not_silently_omitted(text):
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_pdf_transactions_from_page_texts([text])


def bradesco_continuation():
    head = [
        ("Extrato Mensal / Por Período Bradesco", 0.05, 0.03),
        ("Extrato de: Ag: 1234 | CC: 12345-6 | Entre 01/10/2026 e 31/10/2026", 0.05, 0.06),
        ("Data", 0.05, 0.12),
        ("Lançamento", 0.18, 0.12),
        ("Dcto.", 0.49, 0.12),
        ("Crédito (R$)", 0.62, 0.12),
        ("Débito (R$)", 0.73, 0.12),
        ("Saldo (R$)", 0.88, 0.12),
    ]
    return grid_pdf(
        [
            head
            + [
                ("30/09/2026", 0.05, 0.20),
                ("SALDO ANTERIOR", 0.18, 0.20),
                ("100,00", 0.88, 0.20),
                ("01/10/2026", 0.05, 0.28),
                ("PIX RECEBIDO", 0.18, 0.28),
                ("1001", 0.49, 0.28),
                ("50,00", 0.64, 0.28),
                ("150,00", 0.88, 0.28),
                ("Total", 0.18, 0.4),
                ("50,00", 0.64, 0.4),
                ("0,00", 0.75, 0.4),
                ("150,00", 0.88, 0.4),
                ("Últimos Lançamentos", 0.05, 0.55),
            ],
            head[2:]
            + [
                ("01/10/2026", 0.05, 0.20),
                ("SALDO ANTERIOR", 0.18, 0.20),
                ("150,00", 0.88, 0.20),
                ("02/10/2026", 0.05, 0.28),
                ("PIX RECEBIDO", 0.18, 0.28),
                ("1002", 0.49, 0.28),
                ("20,00", 0.64, 0.28),
                ("170,00", 0.88, 0.28),
                ("PIX RECEBIDO", 0.18, 0.34),
                ("1003", 0.49, 0.34),
                ("20,00", 0.64, 0.34),
                ("190,00", 0.88, 0.34),
                ("Total", 0.18, 0.4),
                ("40,00", 0.64, 0.4),
                ("0,00", 0.75, 0.4),
                ("190,00", 0.88, 0.4),
            ],
        ]
    )


def test_repeated_opening_preserves_undated_continuation_rows():
    result = pdf_parser.parse_pdf_transactions(bradesco_continuation())
    assert [(t.date, t.amount) for t in result.transactions] == [("2026-10-01", 50), ("2026-10-02", 20), ("2026-10-02", 20)]
    assert [t.source_page for t in result.canonical_transactions] == [1, 2, 2]


@pytest.mark.parametrize("text", [SAFRA, C6])
def test_native_and_ocr_share_signed_period_policy(text):
    from tests.test_oct20_followup_ocr import ocr

    source = ocr([[(line, 0.05, 0.01 + i * 0.012) for i, line in enumerate(text.splitlines())]])
    visual = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    native = pdf_parser._parse_pdf_transactions_from_page_texts([text])
    assert [(t.date, t.amount) for t in visual.transactions] == [(t.date, t.amount) for t in native.transactions]
    assert all(t.source_page == 1 and t.source_line_refs for t in visual.canonical_transactions)


def test_restarted_pagination_and_new_period_keep_all_years_and_review_gap():
    later = (
        SAFRA.replace("/03/2025", "/05/2025").replace("31/03/2025", "31/05/2025").replace("05/03 ", "05/05 ").replace("06/03 ", "06/05 ")
    )
    result = pdf_parser._parse_pdf_transactions_from_page_texts([SAFRA, later])
    assert len(result.transactions) == 6
    assert [t.source_page for t in result.canonical_transactions] == [1] * 3 + [2] * 3
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert any("statement_blocks_not_contiguous" in t.warnings for t in result.canonical_transactions)


def test_missing_period_year_never_uses_print_date():
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_pdf_transactions_from_page_texts([SAFRA.replace("Período de 01/03/2025 a 31/03/2025", "")])


def test_stone_keeps_description_after_both_currency_cells():
    page = """Extrato de Conta Corrente
stone
Período: 01/10/26 a 31/10/26
DATA TIPO DESCRIÇÃO VALOR SALDO CONTRAPARTE
31/10/26
-R$ 0.12
R$ 116,88
Tarifa Pix maquininha
"""
    result = pdf_parser._parse_pdf_transactions_from_page_texts([page])
    assert [t.amount for t in result.transactions] == [-0.12]
    assert "Tarifa" in result.transactions[0].description


@pytest.mark.parametrize("report", [PORTFOLIO, COLLECTION])
def test_typed_reports_survive_native_pdf_reading(report):
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(report.splitlines()),)))
    assert result.financial_document is not None
    assert "ofx" not in result.financial_document["export"]["available_formats"]


@pytest.mark.parametrize(
    "report",
    [
        COLLECTION.replace("14/10/2026", "14/10/202"),
        COLLECTION.replace("Situação: Sucesso", "Situação: Pendente"),
        PORTFOLIO.replace("R$ 100,00 R$ 50,00 R$ 20,00 R$ 140,00 R$ 2,00 R$ 138,00", "R$ ???"),
    ],
)
def test_unreadable_or_unconfirmed_financial_report_cannot_become_cash(report):
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_pdf_transactions_from_page_texts([report])


def test_collection_total_mismatch_is_preserved_as_review():
    result = pdf_parser._parse_pdf_transactions_from_page_texts(
        [COLLECTION.replace("Total R$ 200,00 R$ 190,00", "Total R$ 200,00 R$ 191,00")]
    )
    assert len(result.transactions) == 2
    assert any(v["status"] == "failed" for v in result.financial_document["validations"])


def test_c6_missing_last_printed_balance_is_not_silently_accepted():
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_pdf_transactions_from_page_texts([C6.replace("R$ 120,00\nInformações sujeitas a alteração", "")])


def test_portfolio_benchmark_without_positions_requires_review():
    benchmark_only = PORTFOLIO[: PORTFOLIO.index("RENDA FIXA")]
    benchmark_only += "Detalhamento da rentabilidade por classe\nCDI 10,00%\n"
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_pdf_transactions_from_page_texts([benchmark_only])
