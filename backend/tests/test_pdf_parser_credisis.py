from app.application import pdf_parser as pdf_parser_module
from app.application.bank_identity import resolve_bank_name
from app.application.layout_profiles.registry import get_layout_profile
from app.application.normalization.balance import uses_descending_running_balance
from app.application.pdf_layout_inference import infer_pdf_layout

CREDISIS_DESCENDING_STATEMENT_TEXT = """
CREDISIS
EXTRATO DE CONTA CORRENTE
PERÍODO: 01/09/2026 A 30/09/2026
DATA ID DESCRIÇÃO VALOR SALDO
SALDO FINAL R$ 1.050,00
30/09/2026 900 PIX RECEBIDO R$ 100,00 R$ 1.050,00
29/09/2026 901 TARIFA EMISSÃO DE BOLETO - R$ 10,00 R$ 950,00
28/09/2026 902 PIX ENVIADO R$ 50,00 R$ 960,00
"""


def test_infer_pdf_layout_recognizes_credisis_descending_statement() -> None:
    result = infer_pdf_layout(CREDISIS_DESCENDING_STATEMENT_TEXT)

    assert result.layout_name == "credisis_extrato_conta_corrente_descendente_v1"
    assert result.confidence >= 0.8
    assert result.used_fallback is False


def test_credisis_layout_requires_brand_anchor() -> None:
    unbranded_statement = CREDISIS_DESCENDING_STATEMENT_TEXT.replace(
        "CREDISIS",
        "COOPERATIVA REGIONAL",
        1,
    )

    assert infer_pdf_layout(unbranded_statement).layout_name != (
        "credisis_extrato_conta_corrente_descendente_v1"
    )


def test_credisis_layout_profile_exposes_bank_and_descending_balance_order() -> None:
    layout_name = "credisis_extrato_conta_corrente_descendente_v1"

    profile = get_layout_profile(layout_name)

    assert profile is not None
    assert profile.bank == "CrediSIS"
    assert profile.schema_version == 2
    assert profile.expected_column_order == ("date", "document", "description", "amount", "balance")
    assert resolve_bank_name(layout_inference_name=layout_name) == "CrediSIS"
    assert uses_descending_running_balance(layout_name) is True


def test_parse_credisis_statement_keeps_descending_balances_without_false_warnings() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        [CREDISIS_DESCENDING_STATEMENT_TEXT]
    )

    assert result.layout.layout_name == "credisis_extrato_conta_corrente_descendente_v1"
    assert [transaction.amount for transaction in result.transactions] == [100.0, -10.0, -50.0]
    assert [row.running_balance for row in result.canonical_transactions] == [1050.0, 950.0, 960.0]
    assert result.parse_metrics["balance_consistency_checked"] == 2
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_warning_count"] == 0
