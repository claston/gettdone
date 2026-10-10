from __future__ import annotations

from app.application import pdf_parser as pdf_parser_module
from app.application.bank_resolver import resolve_bank_code
from app.application.layout_profiles.registry import get_layout_profile
from app.application.pdf_layout_inference import infer_pdf_layout

BB_SISBB_LAYOUT = "banco_do_brasil_sisbb_extrato_conta_v1"


def _sample_page() -> str:
    return """
    Extrato de Conta
    EMPRESA EXEMPLO LTDA
    Nome
    00.000.000/0001-00
    CNPJ
    Agosto/2026
    Posição
    16.09.2026
    Data de emissão
    3413-4
    Agência (prefixo/dv)
    26.323-0
    Conta nº/dv
    Conta Corrente
    Data Dia Histórico Lote Banco Origem Documento Valor - R$ Saldo - R$
    15.07.2026 Saldo anterior 6.617,26 C
    05.08.2026 821-Pix - Recebido 14397 50922019974932 400,00 C 7.017,26 C
    05/08 09:22 00011275286100 CLIENTE UM
    17.08.2026 821-Pix - Recebido 14397 150733284086401 787,00 C
    15/08 07:33 08808173801 CLIENTE DOIS
    17.08.2026 435-Tarifa de Pacote de Serviços 13113 832291100653822 93,10 D 7.711,16 C
    Cobrança referente 17/08/2026
    21.08.2026 870-Transferência recebida 99020 8428 608428000060611 200,00 C 7.911,16 C
    21/08 16:40 CLIENTE TRES
    0,00
    Bloqueado - R$
    7.911,16 C
    Disponível - R$
    Folha 1 Mod. 0.51.291-3 - Jan/2025 - SISBB 25030 - bb.com.br - BB Responde 0800 78 5678 - pvb
    """


def test_recognizes_banco_do_brasil_sisbb_statement() -> None:
    result = infer_pdf_layout(_sample_page())

    assert result.layout_name == BB_SISBB_LAYOUT
    assert result.confidence >= 0.85
    assert result.used_fallback is False


def test_banco_do_brasil_sisbb_profile_exposes_bank_and_parsing_rules() -> None:
    profile = get_layout_profile(BB_SISBB_LAYOUT)

    assert profile is not None
    assert profile.bank == "Banco do Brasil"
    assert profile.schema_version == 2
    assert profile.expected_column_order == ("date", "description", "amount", "balance")
    assert profile.parsing.opening_balance_policy == "skip"
    assert resolve_bank_code(layout_inference_name=BB_SISBB_LAYOUT) == "001"


def test_parses_banco_do_brasil_sisbb_movements_without_opening_balance() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([_sample_page()])

    assert result.layout.layout_name == BB_SISBB_LAYOUT
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert [transaction.amount for transaction in result.transactions] == [400.0, 787.0, -93.1, 200.0]
    assert all(not transaction.description.lower().startswith("saldo anterior") for transaction in result.transactions)
    assert [transaction.running_balance for transaction in result.canonical_transactions] == [
        7017.26,
        None,
        7711.16,
        7911.16,
    ]
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0
