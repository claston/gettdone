from __future__ import annotations

from app.application import pdf_parser as pdf_parser_module
from app.application.errors import InvalidFileContentError
from app.application.pdf_layout_inference import infer_pdf_layout

CORA_NATIVE_PAGE_ONE = """
EMPRESA EXEMPLO LTDA
CNPJ 12.345.678/0001-90
Agência: 0001 - Conta: 1234567-8

Extrato do período                                             01/04/2026 a 30/04/2026

Saldo inicial disponível                                              R$ 0,00
Total de entradas                                               + R$ 2.500,00
Total de saídas                                                  - R$ 2.000,00
Saldo final disponível                                               R$ 500,00

Transações

05/04/2026                                                Saldo do dia R$ 500,00
    Transf Pix recebida         CLIENTE ÔMEGA              + R$ 2.500,00

04/04/2026                                            Saldo do dia - R$ 2.000,00
    Transf Pix enviada          PESSOA TRÊS                   - R$ 500,00
    Boleto pago                 FORNECEDOR DELTA              - R$ 250,00

03/04/2026                                            Saldo do dia - R$ 1.250,00
    Pgto QR Code Pix            EMPRESA GAMA                  - R$ 250,00

02/04/2026                                            Saldo do dia - R$ 1.000,00
    Transf Pix enviada          PESSOA UM                     - R$ 100,00
    Transf Pix enviada          PESSOA DOIS                   - R$ 400,00

01/04/2026                                              Saldo do dia - R$ 500,00

Cora SCFI - CNPJ 37.880.206/0001-63
Ouvidoria: 0800 000 0000 dias úteis, de segunda à sexta
Extrato gerado no dia 30/04/2026 às 10:00                         pág 1 de 2
"""


CORA_NATIVE_PAGE_TWO = """
EMPRESA EXEMPLO LTDA
CNPJ 12.345.678/0001-90
Agência: 0001 - Conta: 1234567-8

    Boleto pago                 FORNECEDOR ALFA              - R$ 200,00
    Boleto pago                 FORNECEDOR BETA              - R$ 300,00

Cora SCFI - CNPJ 37.880.206/0001-63
Ouvidoria: 0800 000 0000 dias úteis, de segunda à sexta
Extrato gerado no dia 30/04/2026 às 10:00                         pág 2 de 2
"""


CORA_FLAT_NATIVE_TEXT = """
EMPRESA EXEMPLO LTDA
CNPJ 12.345.678/0001-90
Agência: 0001 - Conta: 1234567-8
Extrato do período
Saldo inicial disponível
Total de entradas
Total de saídas
Saldo final disponível
Transações
05/04/2026
Transf Pix recebida
04/04/2026
Transf Pix enviada
Boleto pago
03/04/2026
Pgto QR Code Pix
02/04/2026
Transf Pix enviada
Transf Pix enviada
01/04/2026
Boleto pago
Boleto pago
Cora SCFI - CNPJ 37.880.206/0001-63
01/04/2026 a 30/04/2026
R$ 0,00
+ R$ 2.500,00
- R$ 2.000,00
R$ 500,00
- R$ 200,00
- R$ 300,00
- R$ 100,00
- R$ 400,00
- R$ 250,00
- R$ 500,00
- R$ 250,00
+ R$ 2.500,00
"""


def test_infer_pdf_layout_recognizes_native_cora_available_balance_statement() -> None:
    result = infer_pdf_layout(CORA_NATIVE_PAGE_ONE + CORA_NATIVE_PAGE_TWO)

    assert result.layout_name == "cora_extrato_periodo_disponivel_v1"
    assert result.confidence >= 0.95
    assert result.used_fallback is False


def test_parse_native_cora_layout_keeps_cross_page_date_and_daily_balances() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        [CORA_NATIVE_PAGE_ONE, CORA_NATIVE_PAGE_TWO],
        preserve_layout_spacing=True,
    )

    assert result.layout.layout_name == "cora_extrato_periodo_disponivel_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_cora"
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0
    assert [transaction.amount for transaction in result.transactions] == [
        2500.0,
        -500.0,
        -250.0,
        -250.0,
        -100.0,
        -400.0,
        -200.0,
        -300.0,
    ]
    assert result.transactions[-1].date == "2026-04-01"
    assert [
        transaction.running_balance
        for transaction in result.canonical_transactions
        if transaction.running_balance is not None
    ] == [500.0, -2000.0, -1250.0, -1000.0, -500.0]


def test_parse_pdf_recovers_native_cora_layout_before_ocr_when_flat_parse_fails(
    monkeypatch,
) -> None:
    parse_page_texts = pdf_parser_module._parse_pdf_transactions_from_page_texts

    def parse_with_flat_failure(page_texts, **kwargs):
        if page_texts == [CORA_FLAT_NATIVE_TEXT]:
            raise InvalidFileContentError("synthetic flat native parse failure")
        return parse_page_texts(page_texts, **kwargs)

    monkeypatch.setattr(
        pdf_parser_module,
        "_read_native_pdf_page_texts",
        lambda _raw_bytes: [CORA_FLAT_NATIVE_TEXT],
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "_read_layout_native_pdf_page_texts",
        lambda _raw_bytes: [CORA_NATIVE_PAGE_ONE, CORA_NATIVE_PAGE_TWO],
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "_parse_pdf_transactions_from_page_texts",
        parse_with_flat_failure,
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "_extract_pdf_page_texts_with_ocr",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("OCR must not run when native layout text is parseable")
        ),
    )

    result = pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic Cora")

    assert result.layout.layout_name == "cora_extrato_periodo_disponivel_v1"
    assert result.parse_metrics["selected_parser"] == "layout_specific_cora"
    assert len(result.transactions) == 8
    assert result.transactions[-1].date == "2026-04-01"
