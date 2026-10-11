import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.cross_page_transaction_detection import detect_cross_page_transaction_split
from app.application.errors import CrossPageTransactionSplitError


def test_detects_statement_columns_printed_on_separate_pages() -> None:
    evidence = detect_cross_page_transaction_split(
        [
            "\n".join(
                [
                    "BANCO DO BRASIL",
                    "EXTRATO CONTA CORRENTE",
                    "AGENCIA 1234 CONTA 56789-0",
                    "DATA HISTORICO",
                    "01/10/2026 PIX RECEBIDO CLIENTE ALFA",
                    "02/10/2026 PAGAMENTO FORNECEDOR BETA",
                ]
            ),
            "\n".join(["VALOR", "100,00 C", "25,00 D"]),
        ]
    )

    assert evidence is not None
    assert evidence.split_kind == "tiled_columns"
    assert evidence.description_page == 1
    assert evidence.amount_page == 2


def test_detects_single_transaction_split_at_page_boundary() -> None:
    evidence = detect_cross_page_transaction_split(
        [
            "\n".join(
                [
                    "BANCO EXEMPLO",
                    "EXTRATO DA CONTA CORRENTE",
                    "AGENCIA 0001",
                    "DATA HISTORICO VALOR",
                    "01/10/2026 PIX RECEBIDO CLIENTE ALFA",
                ]
            ),
            "\n".join(["EXTRATO - CONTINUACAO", "VALOR", "100,00 C", "SALDO 1.100,00"]),
        ]
    )

    assert evidence is not None
    assert evidence.split_kind == "page_boundary"


def test_does_not_classify_marketplace_report_as_bank_statement() -> None:
    evidence = detect_cross_page_transaction_split(
        [
            "\n".join(
                [
                    "TIKTOK SHOP",
                    "RELATORIO FINANCEIRO",
                    "DATA DO PEDIDO DESCRICAO",
                    "01/10/2026 Pedido 123",
                    "02/10/2026 Pedido 456",
                ]
            ),
            "\n".join(["VALOR LIQUIDO", "100,00", "25,00"]),
        ]
    )

    assert evidence is None


def test_does_not_classify_normal_multi_page_statement() -> None:
    evidence = detect_cross_page_transaction_split(
        [
            "\n".join(
                [
                    "BANCO DO BRASIL",
                    "EXTRATO CONTA CORRENTE",
                    "01/10/2026 PIX RECEBIDO 100,00 C",
                ]
            ),
            "02/10/2026 PAGAMENTO FORNECEDOR 25,00 D",
        ]
    )

    assert evidence is None


def test_does_not_classify_multiline_row_completed_before_page_break() -> None:
    evidence = detect_cross_page_transaction_split(
        [
            "\n".join(
                [
                    "BANCO EXEMPLO",
                    "EXTRATO DA CONTA CORRENTE",
                    "AGENCIA 0001",
                    "01/10/2026 PIX RECEBIDO CLIENTE ALFA",
                    "100,00 C",
                ]
            ),
            "\n".join(["EXTRATO - CONTINUACAO", "SALDO ANTERIOR", "1.100,00"]),
        ]
    )

    assert evidence is None


def test_pdf_parser_rejects_cross_page_statement_instead_of_partial_conversion() -> None:
    pages = [
        "\n".join(
            [
                "BANCO DO BRASIL",
                "EXTRATO CONTA CORRENTE",
                "AGENCIA 1234",
                "DATA HISTORICO",
                "01/10/2026 PIX RECEBIDO CLIENTE ALFA",
                "02/10/2026 PAGAMENTO FORNECEDOR BETA",
            ]
        ),
        "\n".join(["VALOR", "100,00 C", "25,00 D"]),
    ]

    try:
        pdf_parser_module._parse_pdf_transactions_from_page_texts(pages)
    except CrossPageTransactionSplitError as exc:
        assert exc.split_kind == "tiled_columns"
        assert exc.description_page == 1
        assert exc.amount_page == 2
    else:
        raise AssertionError("Expected CrossPageTransactionSplitError")


def test_native_cross_page_split_does_not_retry_with_ocr(monkeypatch) -> None:
    pages = [
        "\n".join(
            [
                "BANCO DO BRASIL",
                "EXTRATO CONTA CORRENTE",
                "AGENCIA 1234",
                "01/10/2026 PIX RECEBIDO CLIENTE ALFA",
                "02/10/2026 PAGAMENTO FORNECEDOR BETA",
            ]
        ),
        "\n".join(["VALOR", "100,00 C", "25,00 D"]),
    ]
    ocr_calls = 0

    def _ocr_stub(*_args, **_kwargs):
        nonlocal ocr_calls
        ocr_calls += 1
        return pages

    monkeypatch.setattr(pdf_parser_module.text_extraction, "read_pdf_creation_month_year", lambda _raw: None)
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda _raw: pages)
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda _raw: pages)
    monkeypatch.setattr(pdf_parser_module, "is_textract_enabled", lambda: False)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts_with_ocr", _ocr_stub)

    with pytest.raises(CrossPageTransactionSplitError) as exc_info:
        pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert ocr_calls == 0
    assert getattr(exc_info.value, "_parse_observability")["ocr_retry_skipped"] == 1
    assert getattr(exc_info.value, "_parse_observability")["ocr_retry_skip_reason"] == (
        "cross_page_transaction_split"
    )

