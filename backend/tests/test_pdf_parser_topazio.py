from app.application import pdf_parser as pdf_parser_module
from app.application.bank_identity import resolve_bank_name
from app.application.bank_resolver import resolve_bank_code
from app.application.pdf_layout_inference import infer_pdf_layout
from app.application.pdf_parser import parse_pdf_transactions

TOPAZIO_FRAGMENTED_STATEMENT_TEXT = """
07/07/2026, 10:35 INTERNET BANK ING - BANC O TOPAZ IO
[TITULAR]
EXTRATO DE CONTA [CONTA]
EXTRATO EMITIDO EM: 07/07/2026 - 10:35
(PERIODO: 01/06/26 - 30/06/26)
AG/CONTA [CONTA] / [IDENTIFICADOR]
30/06 SALDO FINAL R$ 145.222,77
30/06 SALDO DO DIA R$ 145.222,77
30/06 TRANSF. INTERNA C/C MESMA TITULAR [TITULAR]
29/06 TED ENVIADA - CONTRAPARTE A -R$ 50.000,00
17/06 TED ENVIADA - CONTRAPARTE B -R$ 1 11.000,00
16/06 TED DEVOLVIDA - AGENCIA [AGENCIA] CONTA [CONTA] R$ 1 11.000,00
15/06 TARIFA TRANSFERENCIA TED -R$ 9,90
HTTPS ://IBANK .BANC OTOPAZ IO.C OM.BR/AC C OUNT/AC C OUNT-BALANC E-DETAILED 1/1
"""


def test_infer_pdf_layout_recognizes_topazio_statement_with_fragmented_brand() -> None:
    result = infer_pdf_layout(TOPAZIO_FRAGMENTED_STATEMENT_TEXT)

    assert result.layout_name == "banco_topazio_extrato_conta_corrente_lista_v1"
    assert result.confidence >= 0.8
    assert result.used_fallback is False


def test_topazio_profile_resolves_bank_identity_and_code() -> None:
    layout_name = "banco_topazio_extrato_conta_corrente_lista_v1"

    assert resolve_bank_name(layout_inference_name=layout_name) == "Banco Topázio"
    assert resolve_bank_code(layout_inference_name=layout_name) == "082"


def test_parse_topazio_fragmented_statement_preserves_signs_and_ocr_split_amounts(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_native_pdf_page_texts",
        lambda raw_bytes: [TOPAZIO_FRAGMENTED_STATEMENT_TEXT],
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_layout_native_pdf_page_texts",
        lambda raw_bytes: [TOPAZIO_FRAGMENTED_STATEMENT_TEXT],
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "banco_topazio_extrato_conta_corrente_lista_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_topazio"
    assert [transaction.amount for transaction in result.transactions] == [-50000.0, -111000.0, 111000.0, -9.9]
    assert [transaction.date for transaction in result.transactions] == [
        "2026-06-29",
        "2026-06-17",
        "2026-06-16",
        "2026-06-15",
    ]
    assert all("SALDO" not in transaction.description.upper() for transaction in result.transactions)


def test_infer_pdf_layout_does_not_identify_topazio_from_generic_account_header() -> None:
    text = """
    EXTRATO EMITIDO EM: 07/07/2026 - 10:35
    PERIODO: 01/06/26 - 30/06/26
    AG/CONTA [CONTA] / [IDENTIFICADOR]
    29/06 TED ENVIADA - CONTRAPARTE A -R$ 50.000,00
    29/06 TARIFA TRANSFERENCIA TED -R$ 9,90
    """

    assert infer_pdf_layout(text).layout_name != "banco_topazio_extrato_conta_corrente_lista_v1"
