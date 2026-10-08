from app.application import pdf_parser as pdf_parser_module
from app.application.bank_identity import resolve_bank_name
from app.application.bank_resolver import resolve_bank_code
from app.application.layout_profiles.registry import get_layout_profile
from app.application.pdf_layout_inference import infer_pdf_layout
from app.application.pdf_parser import parse_pdf_transactions

BANESTES_INTERNET_BANKING_TEXT = """
SALDO TOTAL
R$ 125.000,00
CHEQUE ESPECIAL DISPONÍVEL
R$ 10.000,00
ENTRADAS E SAÍDAS
AGÊNCIA: 01-AGENCIA DE NEGOCIOS
CONTA: 1234567 - 8
CLIENTE: EMPRESA EXEMPLO LTDA
PERÍODO: 01/05/2026 À 31/05/2026
DATA LANÇAMENTO VALOR (R$)
20
MAI
SALDO ANTERIOR 100.000,00
PIX RECEBIDO 20/05/2026-10:30:00 EMPRESA PAGADORA
COMERCIO LTDA
10.000,00

PAGAMENTO TÍTULO OUTROS BANCOS -1.500,00

SALDO CONTA/RENDE+ 108.500,00
21
MAI
RENDIMENTO DE RESGATE 0,05
PIX ENVIADO 21/05/2026-09:15:00 FORNECEDOR EXEMPLO
SERVICOS LTDA
-2.000,00

SALDO CONTA/RENDE+ 106.500,05
LANÇAMENTOS PREVISTOS
DÉBITO TEF 12345678 -750,00
SALDOS
SALDO CONTA CORRENTE -750,00
21/05/26, 11:20 Banestes Internet Banking
https://wwws.banestes.b.br/netib/UrlCertificado?url=logininetbank 1/1
"""


def test_infer_pdf_layout_recognizes_banestes_internet_banking_statement() -> None:
    result = infer_pdf_layout(BANESTES_INTERNET_BANKING_TEXT)

    assert result.layout_name == "banestes_internet_banking_extrato_v1"
    assert result.confidence >= 0.75
    assert result.used_fallback is False


def test_banestes_layout_profile_resolves_bank_identity_and_code() -> None:
    layout_name = "banestes_internet_banking_extrato_v1"
    profile = get_layout_profile(layout_name)

    assert profile is not None
    assert profile.bank == "Banestes"
    assert profile.schema_version == 2
    assert profile.expected_column_order == ("date", "description", "amount")
    assert profile.parsing.opening_balance_policy == "skip"
    assert resolve_bank_name(layout_inference_name=layout_name) == "Banestes"
    assert resolve_bank_code(layout_inference_name=layout_name) == "021"


def test_parse_banestes_internet_banking_statement_groups_day_sections(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_native_pdf_page_texts",
        lambda raw_bytes: [BANESTES_INTERNET_BANKING_TEXT],
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_layout_native_pdf_page_texts",
        lambda raw_bytes: [BANESTES_INTERNET_BANKING_TEXT],
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "banestes_internet_banking_extrato_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_banestes"
    assert [transaction.date for transaction in result.transactions] == [
        "2026-05-20",
        "2026-05-20",
        "2026-05-21",
        "2026-05-21",
    ]
    assert [transaction.amount for transaction in result.transactions] == [10000.0, -1500.0, 0.05, -2000.0]
    assert result.transactions[0].description.endswith("EMPRESA PAGADORA COMERCIO LTDA")
    assert result.transactions[3].description.endswith("FORNECEDOR EXEMPLO SERVICOS LTDA")
    assert all("SALDO" not in transaction.description for transaction in result.transactions)
