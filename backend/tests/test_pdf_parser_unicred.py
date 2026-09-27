from app.application import pdf_parser as pdf_parser_module
from app.application.bank_identity import resolve_bank_name
from app.application.bank_resolver import resolve_bank_code
from app.application.layout_profiles.registry import get_layout_profile
from app.application.pdf_layout_inference import infer_pdf_layout
from app.application.pdf_parser import parse_pdf_transactions

UNICRED_MODERN_STATEMENT_TEXT = """
25/09/2026 15:05:23
EXTRATO
[TITULAR]
PERIODO DE 01/08/2026 A 31/08/2026 COOP: 544 - AG: [IDENTIFICADOR] - CONTA [CONTA]
SALDO EM 31/07/2026: -R$ 8.269,69 TOTAL DISPONIVEL R$ 19.637,04
LIMITE DE CHEQUE ESPECIAL R$ 22.500,00
DATA LANCAMENTOS VALOR (R$) SALDO (R$)
03/08/2026 PJ CONTA [CONTA] 1 ( DOC.: 0 ) - R$ 12,50 -R$ 8.282,19
03/08/2026 IOF ( DOC.: SALDO DEV ) - R$ 21,41 -R$ 8.303,60
05/08/2026 LIQUIDACAO DE TITULO DE FINANCIAMENTO ( DOC.: 12345 ) - R$ 1.724,84 -R$ 10.028,44
07/08/2026 CREDITO RECEBIMENTO DE PIX ( DOC.: CRED PIX / R$ 10.000,00 -R$ 28,44
[PARTE] )
17/08/2026 DEBITO PORTO SEGURO CONSORCIO ( DOC.: 98765 ) - R$ 1.130,02 -R$ 1.158,46
31/08/2026 JUROS CHEQUE ESPECIAL - PJ ( DOC.: 0 ) - R$ 281,16 -R$ 1.439,62
SALDO NO FINAL DO PERIODO -R$ 1.439,62
SALDO ATUAL -R$ 2.862,96
TOTAL DISPONIVEL R$ 19.637,04
SALDO BLOQUEADO R$ 0,00
LIMITE DE CHEQUE ESPECIAL R$ 22.500,00
LANCAMENTOS FUTUROS R$ 0,00
CENTRAL DE RELACIONAMENTO: CAPITAIS E REGIOES METROPOLITANAS: 3003 7703 - DEMAIS REGIOES: 0800 200 7302
NO EXTERIOR: +55 11 3003 7703 - SAC: 0800 647 2930 - OUVIDORIA: 0800 940 0602
"""


def test_infer_pdf_layout_recognizes_unicred_modern_statement_without_visible_logo() -> None:
    result = infer_pdf_layout(UNICRED_MODERN_STATEMENT_TEXT.replace("3003 7703", "[IDENTIFICADOR]"))

    assert result.layout_name == "unicred_extrato_conta_corrente_moderno_v1"
    assert result.confidence >= 0.75
    assert result.used_fallback is False


def test_unicred_modern_layout_profile_exposes_bank_and_parsing_rules() -> None:
    profile = get_layout_profile("unicred_extrato_conta_corrente_moderno_v1")

    assert profile is not None
    assert profile.bank == "Unicred"
    assert profile.schema_version == 2
    assert profile.expected_column_order == ("date", "description", "amount", "balance")
    assert profile.parsing.opening_balance_policy == "skip"


def test_unicred_modern_layout_resolves_bank_identity_and_code() -> None:
    layout_name = "unicred_extrato_conta_corrente_moderno_v1"

    assert resolve_bank_name(layout_inference_name=layout_name) == "Unicred"
    assert resolve_bank_code(layout_inference_name=layout_name) == "136"


def test_parse_unicred_modern_statement_preserves_credit_sign_and_running_balance(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_native_pdf_page_texts",
        lambda raw_bytes: [UNICRED_MODERN_STATEMENT_TEXT],
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_layout_native_pdf_page_texts",
        lambda raw_bytes: [UNICRED_MODERN_STATEMENT_TEXT],
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "unicred_extrato_conta_corrente_moderno_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_unicred"
    assert len(result.transactions) == 6
    assert [transaction.amount for transaction in result.transactions] == [
        -12.5,
        -21.41,
        -1724.84,
        10000.0,
        -1130.02,
        -281.16,
    ]
    assert result.transactions[3].description.startswith("CREDITO RECEBIMENTO DE PIX")
    assert result.canonical_transactions[3].running_balance == -28.44
    assert all("SALDO ATUAL" not in transaction.description for transaction in result.transactions)


def test_unicred_modern_layout_does_not_capture_other_cooperative_table() -> None:
    text = """
    SICREDI
    EXTRATO DE CONTA CORRENTE
    COOPERATIVA 0116 AGENCIA 0730 CONTA CORRENTE 12345
    DATA DOCUMENTO HISTORICO DEBITO CREDITO SALDO
    01/08/2026 123 PIX RECEBIDO 100,00 1.100,00
    02/08/2026 124 PIX ENVIADO 50,00 1.050,00
    """

    assert infer_pdf_layout(text).layout_name != "unicred_extrato_conta_corrente_moderno_v1"
