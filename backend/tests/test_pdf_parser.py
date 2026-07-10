import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.document_extraction_models import RawDocumentExtraction
from app.application.errors import InvalidFileContentError
from app.application.pdf_parser import parse_pdf_transactions
from app.application.textract_transaction_adapter import TextractTransactionExtractionResult
from tests.fixtures.pdf_golden_samples import (
    GROUPED_INLINE_MULTILINE_SAMPLE,
    UNICODE_MINUS_SINGLE_ROW_SAMPLE,
    VIACREDI_TABULAR_BALANCE_FAIL,
    VIACREDI_TABULAR_BALANCE_OK,
)


@pytest.mark.parametrize(
    ("raw_line", "expected_date", "expected_description", "expected_amount"),
    [
        ("01-07-2026 TESTE BR 1.234,56", "2026-07-01", "TESTE BR", 1234.56),
        ("2026-07-02 TESTE US 1,234.56", "2026-07-02", "TESTE US", 1234.56),
        ("03.07.2026 TESTE SPACE 1 234,56", "2026-07-03", "TESTE SPACE", 1234.56),
    ],
)
def test_parse_pdf_transactions_preserves_complete_date_and_amount_candidates(
    raw_line: str,
    expected_date: str,
    expected_description: str,
    expected_amount: float,
) -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([raw_line])

    assert len(result.transactions) == 1
    assert result.transactions[0].date == expected_date
    assert result.transactions[0].description == expected_description
    assert result.transactions[0].amount == expected_amount


def test_parse_pdf_transactions_handles_inline_and_multiline_amount_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [GROUPED_INLINE_MULTILINE_SAMPLE]
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
    assert len(result.canonical_transactions) == 2
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert result.transactions[0].date == "2026-03-16"
    assert result.transactions[0].amount == -240.24
    assert result.transactions[1].date == "2026-03-25"
    assert result.transactions[1].amount == -241.05
    assert result.canonical_transactions[0].layout_name == result.layout.layout_name
    assert result.canonical_transactions[0].confidence == result.layout.confidence
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 2
    assert result.canonical_transactions[0].source_parser == "grouped"
    assert result.parse_metrics["canonical_source_parser_grouped_count"] == 2
    assert result.parse_metrics["canonical_source_parser_types"] == "grouped"


def test_parse_pdf_transactions_respects_credit_column_before_description_hint() -> None:
    text = """
    Extrato Santander Negócios & Empresas - Saldo Coerente
    Santander Negócios & Empresas
    Resumo - março/2021
    Conta Corrente
    Movimentação
    Data Descrição Nº Documento Créditos Débitos Saldo
              SALDO EM 28/02 0,00
    01/03     PAGAMENTO CARTAO DE DEBITO 585269          3.006,98       3.006,98
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.layout.layout_name == "santander_negocios_empresas_extrato_consolidado_inteligente_conta_corrente_v1"
    assert result.transactions == [
        pdf_parser_module.NormalizedTransaction(
            date="2026-03-01",
            description="PAGAMENTO CARTAO DE DEBITO 585269",
            amount=3006.98,
            type="inflow",
        )
    ]
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_parse_pdf_transactions_respects_credit_column_when_row_has_no_running_balance() -> None:
    text = """
    Extrato Santander Negócios & Empresas - Saldo Coerente
    Santander Negócios & Empresas
    Resumo - março/2021
    Conta Corrente
    Movimentação
    Data      Descrição                                Nº Documento        Créditos        Débitos        Saldo
              SALDO EM 28/02                                                                             0,00
    01/03     PAGAMENTO CARTAO DE DEBITO                   585269          3.006,98
              GETNET-ELO DEBITO
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.layout.layout_name == "santander_negocios_empresas_extrato_consolidado_inteligente_conta_corrente_v1"
    assert result.transactions == [
        pdf_parser_module.NormalizedTransaction(
            date="2026-03-01",
            description="PAGAMENTO CARTAO DE DEBITO 585269",
            amount=3006.98,
            type="inflow",
        )
    ]


def test_parse_pdf_transactions_keeps_grouped_transaction_when_standalone_minus_sits_between_description_and_amount() -> None:
    text = """
    Santander
    Negócios
    & Empresas
    EXTRATO CONSOLIDADO INTELIGENTE
    Resumo - março/2021
    Conta Corrente
    Movimentação
    Data
    Descrição
    Nº Documento
    Movimentos (R$)
    Saldo (R$)
    Créditos
    Débitos
    SALDO EM 28/02
    0,00
    01/03
    TARIFA RECOLHIMENTO DE VALORES
    -
    257,62-
    PAGAMENTO CARTAO DE DEBITO
    GETNET-ELO DEBITO
    585269
    3.006,98
    PAGAMENTO CARTAO DE DEBITO
    GETNET-MASTERCARD
    585269
    3.380,36
    6.129,72
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.transactions == [
        pdf_parser_module.NormalizedTransaction(
            date="2026-03-01",
            description="TARIFA RECOLHIMENTO DE VALORES",
            amount=-257.62,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2026-03-01",
            description="PAGAMENTO CARTAO DE DEBITO GETNET-ELO DEBITO 585269",
            amount=-3006.98,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2026-03-01",
            description="PAGAMENTO CARTAO DE DEBITO GETNET-MASTERCARD 585269",
            amount=-3380.36,
            type="outflow",
        ),
    ]


def test_parse_pdf_transactions_skips_invalid_inline_date_candidate_and_keeps_valid_rows() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        ["00/00/0000 LANCAMENTO INVALIDO 10,00\n10/04/2026 PIX RECEBIDO 25,00"]
    )

    assert len(result.transactions) == 1
    assert result.transactions[0].date == "2026-04-10"
    assert result.transactions[0].description == "PIX RECEBIDO"
    assert result.transactions[0].amount == 25.0
    assert result.parse_metrics["invalid_date_candidates_skipped"] == 1


def test_parse_pdf_transactions_handles_itau_spaced_month_slash_lines() -> None:
    text = """
    Itaú
    Empresas
    Itaú
    agência
    conta corrente
    Saldo resumido
    descrição
    saldo (R$)
    saldo em conta corrente
    13.043,86
    total para saque
    13.043,86
    Extrato conta corrente / Lançamentos
    período: 01/07/2021 até 31/07/2021
    data
    lançamentos
    ag/origem
    valor (R$)
    saldo (R$)
    01 / jul
    SALDO INICIAL
    10,00
    01 / jul
    TRANSF TITUL TED
    4015
    -109,50
    -99,50
    02 / jul
    CREDITO TED
    2371
    5.250,00
    5.150,50
    05 / jul
    PIX RECEBIDO CLIENTE
    0000
    3.750,20
    8.900,70
    08 / jul
    SISPAG FORNECEDOR
    4015
    -680,00
    8.220,70
    12 / jul
    RECEBIMENTO CARTAO
    9999
    2.416,45
    10.637,15
    16 / jul
    TARIFA PACOTE SERVICOS
    4015
    -43,29
    10.593,86
    23 / jul
    CREDITO COBRANCA
    2371
    2.450,00
    13.043,86
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.layout.layout_name == "itau_empresas_extrato_lancamentos_conta_corrente_v1"
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert len(result.transactions) == 7
    assert result.transactions[0] == pdf_parser_module.NormalizedTransaction(
        date="2021-07-01",
        description="TRANSF TITUL TED 4015",
        amount=-109.5,
        type="outflow",
    )
    assert result.transactions[-1] == pdf_parser_module.NormalizedTransaction(
        date="2021-07-23",
        description="CREDITO COBRANCA 2371",
        amount=2450.0,
        type="inflow",
    )
    assert all("SALDO INICIAL" not in tx.description for tx in result.transactions)


def test_parse_pdf_transactions_skips_invalid_date_only_candidate_and_keeps_grouped_rows() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        ["00/00/0000\nLINHA INVALIDA\n10/04/2026\nPIX RECEBIDO\n25,00"]
    )

    assert len(result.transactions) == 1
    assert result.transactions[0].date == "2026-04-10"
    assert result.transactions[0].description == "PIX RECEBIDO"
    assert result.transactions[0].amount == 25.0
    assert result.parse_metrics["invalid_date_candidates_skipped"] == 1


def test_parse_pdf_transactions_ignores_placeholder_year_when_valid_row_has_no_year() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        ["00/00/0000 LANCAMENTO INVALIDO 10,00\n10/04 PIX RECEBIDO 25,00"]
    )

    assert len(result.transactions) == 1
    assert result.transactions[0].date.endswith("-04-10")
    assert result.transactions[0].description == "PIX RECEBIDO"
    assert result.parse_metrics["invalid_date_candidates_skipped"] == 1


def test_parse_pdf_transactions_rejects_document_with_only_invalid_date_candidates() -> None:
    with pytest.raises(InvalidFileContentError, match="no recognizable transaction row pattern"):
        pdf_parser_module._parse_pdf_transactions_from_page_texts(["00/00/0000 LANCAMENTO INVALIDO 10,00"])


def test_parse_pdf_transactions_parses_unicode_minus_with_currency_prefix(monkeypatch) -> None:
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [UNICODE_MINUS_SINGLE_ROW_SAMPLE])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 1
    assert len(result.canonical_transactions) == 1
    assert result.transactions[0].date == "2026-04-10"
    assert result.transactions[0].amount == -10.0
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 1
    assert result.canonical_transactions[0].source_parser == "grouped"


def test_parse_pdf_transactions_skips_invalid_date_candidate_from_native_text(monkeypatch) -> None:
    page_texts = ["00/00/0000 LANCAMENTO INVALIDO 10,00\n10/04/2026 PIX RECEBIDO 25,00"]
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: page_texts)

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 1
    assert result.transactions[0].date == "2026-04-10"
    assert result.transactions[0].amount == 25.0
    assert result.parse_metrics["invalid_date_candidates_skipped"] == 1


def test_parse_pdf_transactions_uses_ocr_fallback_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("PDF_OCR_ENABLED", "true")
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(
        pdf_parser_module,
        "extract_pdf_page_texts_with_ocr",
        lambda raw_bytes: ["00/00/0000 LANCAMENTO INVALIDO 10,00\n10/04 PIX 10,00"],
    )

    result = parse_pdf_transactions(b"%PDF synthetic")
    assert len(result.transactions) == 1
    assert result.transactions[0].date == "2026-04-10"
    assert result.transactions[0].amount == 10.0
    assert result.parse_metrics["invalid_date_candidates_skipped"] == 1


def test_parse_pdf_transactions_retries_with_ocr_when_native_text_is_empty(monkeypatch) -> None:
    progress: list[tuple[int, int]] = []

    def _ocr_stub(raw_bytes, on_progress):
        on_progress(6, 6)
        return ["10/04/2026 PIX RECEBIDO 50,00"]

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(pdf_parser_module, "_read_pdf_page_count", lambda raw_bytes: 6)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", _ocr_stub)

    result = parse_pdf_transactions(
        b"%PDF synthetic",
        on_ocr_progress=lambda current, total: progress.append((current, total)),
        max_ocr_pages=6,
    )

    assert len(result.transactions) == 1
    assert result.transactions[0].amount == 50.0
    assert result.parse_metrics["ocr_retry_reason"] == "insufficient_native_text"
    assert progress == [(6, 6)]


def test_parse_pdf_transactions_enforces_page_limit_before_empty_native_text_ocr_retry(monkeypatch) -> None:
    calls = {"ocr": 0}

    def _ocr_stub(raw_bytes):
        calls["ocr"] += 1
        return ["10/04/2026 PIX RECEBIDO 50,00"]

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(pdf_parser_module, "_read_pdf_page_count", lambda raw_bytes: 6)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", _ocr_stub)

    with pytest.raises(pdf_parser_module.MaxPagesPerFileExceededError):
        parse_pdf_transactions(b"%PDF synthetic", max_ocr_pages=5)

    assert calls["ocr"] == 0


def test_parse_pdf_transactions_keeps_insufficient_text_error_when_empty_native_text_ocr_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(pdf_parser_module, "_read_pdf_page_count", lambda raw_bytes: 6)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: False)

    with pytest.raises(InvalidFileContentError, match="does not contain extractable text"):
        parse_pdf_transactions(b"%PDF synthetic", max_ocr_pages=6)


def test_parse_pdf_transactions_uses_textract_adapter_path_for_scanned_pdf_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: False)

    class _GatewayStub:
        def analyze_pdf(self, *, raw_bytes: bytes) -> dict[str, object]:
            _ = raw_bytes
            return {
                "document_hash": "hash",
                "page_count": 1,
                "blocks": [
                    {
                        "BlockType": "LINE",
                        "Id": "l1",
                        "Page": 1,
                        "Text": "01/04/2026 PIX RECEBIDO 10,00",
                        "Confidence": 99.0,
                    }
                ],
                "metrics": {"textract_total_ms": 120.0},
            }

    monkeypatch.setattr(pdf_parser_module, "TextractGateway", lambda: _GatewayStub())

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 1
    assert result.transactions[0].amount == 10.0
    assert result.parse_metrics.get("extraction_provider") == "aws_textract"
    assert result.parse_metrics.get("textract_used") == 1


def test_parse_pdf_transactions_uses_standard_pdf_parser_for_textract_text_mode(monkeypatch) -> None:
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: False)

    class _GatewayStub:
        def analyze_pdf(self, *, raw_bytes: bytes) -> dict[str, object]:
            _ = raw_bytes
            return {
                "document_hash": "hash",
                "page_count": 1,
                "blocks": [
                    {
                        "BlockType": "LINE",
                        "Id": "l1",
                        "Page": 1,
                        "Text": "Data Historico Documento Valor Saldo",
                        "Confidence": 99.0,
                    },
                    {
                        "BlockType": "LINE",
                        "Id": "l2",
                        "Page": 1,
                        "Text": "29/02/2024 SALDO ANTERIOR -441,66 -441,66",
                        "Confidence": 99.0,
                    },
                    {
                        "BlockType": "LINE",
                        "Id": "l3",
                        "Page": 1,
                        "Text": "01/03/2024 DEPOSITO IDENTIFICADO 010324854 289,73 -151,93",
                        "Confidence": 99.0,
                    },
                ],
                "metrics": {"textract_total_ms": 120.0, "textract_mode": "text"},
            }

    monkeypatch.setattr(pdf_parser_module, "TextractGateway", lambda: _GatewayStub())

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
    assert result.transactions[0].description == "SALDO ANTERIOR"
    assert result.transactions[1].description == "DEPOSITO IDENTIFICADO 010324854"
    assert result.canonical_transactions[0].running_balance == -441.66
    assert result.canonical_transactions[1].running_balance == -151.93
    assert result.parse_metrics.get("selected_parser") == "tabular"
    assert result.parse_metrics.get("extraction_provider") == "aws_textract"
    assert result.parse_metrics.get("textract_mode") == "text"
    assert result.canonical_transactions[0].warnings == []
    assert result.canonical_transactions[1].warnings == []


def test_parse_pdf_transactions_uses_textract_when_forced_even_with_native_text(monkeypatch) -> None:
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "true")
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: ["native text exists"])
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: False)

    class _GatewayStub:
        def analyze_pdf(self, *, raw_bytes: bytes) -> dict[str, object]:
            _ = raw_bytes
            return {
                "document_hash": "hash",
                "page_count": 1,
                "blocks": [{"BlockType": "LINE", "Id": "l1", "Page": 1, "Text": "01/04/2026 PIX 10,00"}],
                "metrics": {},
            }

    monkeypatch.setattr(pdf_parser_module, "TextractGateway", lambda: _GatewayStub())

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 1
    assert result.parse_metrics.get("extraction_provider") == "aws_textract"


def test_parse_pdf_transactions_falls_back_to_local_ocr_when_textract_fails(monkeypatch) -> None:
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)

    class _FailingGatewayStub:
        def analyze_pdf(self, *, raw_bytes: bytes) -> dict[str, object]:
            _ = raw_bytes
            raise pdf_parser_module.InvalidFileContentError("provider failed")

    monkeypatch.setattr(pdf_parser_module, "TextractGateway", lambda: _FailingGatewayStub())
    monkeypatch.setattr(
        pdf_parser_module,
        "extract_pdf_page_texts_with_ocr",
        lambda raw_bytes, on_progress=None: ["01/04/2026 PIX RECEBIDO 10,00"],
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 1
    assert result.transactions[0].amount == 10.0
    assert result.parse_metrics.get("textract_attempted") == 1
    assert result.parse_metrics.get("textract_used") == 0
    assert result.parse_metrics.get("textract_error_type") == "InvalidFileContentError"
    assert result.parse_metrics.get("native_text_detected") == 0


def test_parse_pdf_transactions_textract_path_applies_balance_consistency_check(monkeypatch) -> None:
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: False)

    class _GatewayStub:
        def analyze_pdf(self, *, raw_bytes: bytes) -> dict[str, object]:
            _ = raw_bytes
            return {"document_hash": "hash", "page_count": 1, "blocks": [], "metrics": {}}

    monkeypatch.setattr(pdf_parser_module, "TextractGateway", lambda: _GatewayStub())
    monkeypatch.setattr(
        pdf_parser_module,
        "map_textract_blocks_to_extraction",
        lambda document_hash, blocks, page_count: RawDocumentExtraction(
            provider="aws_textract",
            document_hash=document_hash,
            pages=[],
            metrics={},
        ),
    )

    adapted = TextractTransactionExtractionResult(
        transactions=[
            pdf_parser_module.NormalizedTransaction(date="2024-03-01", description="A", amount=100.0, type="inflow"),
            pdf_parser_module.NormalizedTransaction(date="2024-03-02", description="B", amount=-10.0, type="outflow"),
        ],
        canonical_transactions=[
            pdf_parser_module.CanonicalTransaction(
                date="2024-03-01",
                description="A",
                amount=100.0,
                type="inflow",
                running_balance=100.0,
                source_parser="textract_table",
            ),
            pdf_parser_module.CanonicalTransaction(
                date="2024-03-02",
                description="B",
                amount=-10.0,
                type="outflow",
                running_balance=95.0,
                source_parser="textract_table",
            ),
        ],
        extracted_text="A\nB",
        parse_metrics={"selected_parser": "textract_table", "transaction_count": 2},
    )
    monkeypatch.setattr(pdf_parser_module, "adapt_textract_extraction_to_transactions", lambda extraction: adapted)

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics.get("balance_consistency_checked") == 1
    assert result.parse_metrics.get("balance_consistency_failed") == 1
    assert "balance_consistency_failed" in result.canonical_transactions[1].warnings


def test_parse_pdf_transactions_retries_with_ocr_when_native_is_generic_low_coverage(monkeypatch) -> None:
    native_pages = ["native page 1", "native page 2", "native page 3", "native page 4", "native page 5", "native page 6"]
    ocr_pages = ["ocr page 1", "ocr page 2", "ocr page 3", "ocr page 4", "ocr page 5", "ocr page 6"]
    native_result = pdf_parser_module.PdfParseResult(
        transactions=[
            pdf_parser_module.NormalizedTransaction(
                date="2024-03-01",
                description="TX 1",
                amount=10.0,
                type="inflow",
            ),
            pdf_parser_module.NormalizedTransaction(
                date="2024-03-02",
                description="TX 2",
                amount=-5.0,
                type="outflow",
            ),
        ],
        layout=pdf_parser_module.PdfLayoutInference(
            layout_name="generic_statement_ptbr",
            confidence=0.25,
            used_fallback=True,
        ),
        extracted_text="native",
        parse_metrics={"confidence_band": "low", "balance_consistency_failed": 2},
    )
    ocr_result = pdf_parser_module.PdfParseResult(
        transactions=[
            pdf_parser_module.NormalizedTransaction(
                date="2024-03-01",
                description="A",
                amount=10.0,
                type="inflow",
            ),
            pdf_parser_module.NormalizedTransaction(
                date="2024-03-02",
                description="B",
                amount=-5.0,
                type="outflow",
            ),
            pdf_parser_module.NormalizedTransaction(
                date="2024-03-03",
                description="C",
                amount=15.0,
                type="inflow",
            ),
            pdf_parser_module.NormalizedTransaction(
                date="2024-03-04",
                description="D",
                amount=-3.0,
                type="outflow",
            ),
            pdf_parser_module.NormalizedTransaction(
                date="2024-03-05",
                description="E",
                amount=2.0,
                type="inflow",
            ),
        ],
        layout=pdf_parser_module.PdfLayoutInference(
            layout_name="bradesco_net_empresa_extrato_mensal_por_periodo_v1",
            confidence=0.86,
            used_fallback=False,
        ),
        extracted_text="ocr",
        parse_metrics={"confidence_band": "medium", "balance_consistency_failed": 0},
    )

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: native_pages)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", lambda raw_bytes: ocr_pages)
    monkeypatch.setattr(
        pdf_parser_module,
        "_parse_pdf_transactions_from_page_texts",
        lambda pages: native_result if pages == native_pages else ocr_result,
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.transactions == ocr_result.transactions
    assert result.extracted_text == ocr_result.extracted_text
    assert result.layout == ocr_result.layout
    assert result.parse_metrics.get("textract_attempted") == 0
    assert result.parse_metrics.get("native_text_detected") == 1
    assert len(result.transactions) == 5


def test_parse_pdf_transactions_retries_with_ocr_when_native_parse_fails(monkeypatch) -> None:
    native_pages = ["native page 1", "native page 2"]
    ocr_pages = ["01/04/2026 PIX RECEBIDO 10,00"]
    native_error = InvalidFileContentError("native parse failed")
    parse_pages = pdf_parser_module._parse_pdf_transactions_from_page_texts
    progress: list[tuple[int, int]] = []

    def _ocr_stub(raw_bytes, on_progress):
        on_progress(1, 2)
        return ocr_pages

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: native_pages)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", _ocr_stub)

    def _parse_pages(pages):
        if pages == native_pages:
            raise native_error
        return parse_pages(ocr_pages)

    monkeypatch.setattr(pdf_parser_module, "_parse_pdf_transactions_from_page_texts", _parse_pages)

    result = parse_pdf_transactions(b"%PDF synthetic", on_ocr_progress=lambda current, total: progress.append((current, total)))

    assert len(result.transactions) == 1
    assert result.transactions[0].amount == 10.0
    assert result.parse_metrics["ocr_retry_reason"] == "native_parse_failed"
    assert progress == [(1, 2)]


def test_parse_pdf_transactions_keeps_native_parse_error_when_ocr_is_disabled(monkeypatch) -> None:
    native_pages = ["native page 1", "native page 2"]
    native_error = InvalidFileContentError("native parse failed")

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: native_pages)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: False)
    monkeypatch.setattr(pdf_parser_module, "_parse_pdf_transactions_from_page_texts", lambda pages: (_ for _ in ()).throw(native_error))

    with pytest.raises(InvalidFileContentError, match="native parse failed"):
        parse_pdf_transactions(b"%PDF synthetic")


def test_parse_pdf_transactions_enforces_page_limit_before_native_failure_ocr_retry(monkeypatch) -> None:
    native_pages = ["native page 1", "native page 2", "native page 3"]
    native_error = InvalidFileContentError("native parse failed")
    calls = {"ocr": 0}

    def _ocr_stub(raw_bytes):
        calls["ocr"] += 1
        return ["01/04/2026 PIX RECEBIDO 10,00"]

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: native_pages)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", _ocr_stub)
    monkeypatch.setattr(pdf_parser_module, "_parse_pdf_transactions_from_page_texts", lambda pages: (_ for _ in ()).throw(native_error))

    with pytest.raises(pdf_parser_module.MaxPagesPerFileExceededError):
        parse_pdf_transactions(b"%PDF synthetic", max_ocr_pages=2)

    assert calls["ocr"] == 0


def test_parse_pdf_transactions_keeps_native_parse_error_when_failure_ocr_retry_is_empty(monkeypatch) -> None:
    native_pages = ["native page 1", "native page 2"]
    native_error = InvalidFileContentError("native parse failed")

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: native_pages)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", lambda raw_bytes: [])
    monkeypatch.setattr(pdf_parser_module, "_parse_pdf_transactions_from_page_texts", lambda pages: (_ for _ in ()).throw(native_error))

    with pytest.raises(InvalidFileContentError, match="native parse failed"):
        parse_pdf_transactions(b"%PDF synthetic")


def test_parse_pdf_transactions_skips_ocr_when_native_failure_is_date_pattern_only(monkeypatch) -> None:
    native_pages = ["native page 1", "native page 2"]
    native_error = InvalidFileContentError(
        "PDF text was extracted, but no recognizable transaction row pattern was found."
        " diagnostics: has_date_like=0 has_amount_like=1 inline_candidates=0"
        " tabular_candidates=0 columnar_candidates=0 missing_signals=date_pattern,transaction_row_pattern"
    )
    calls = {"ocr": 0}

    def _ocr_stub(raw_bytes):
        calls["ocr"] += 1
        return ["01/04/2026 PIX RECEBIDO 10,00"]

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: native_pages)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", _ocr_stub)
    monkeypatch.setattr(pdf_parser_module, "_parse_pdf_transactions_from_page_texts", lambda pages: (_ for _ in ()).throw(native_error))

    with pytest.raises(InvalidFileContentError, match="missing_signals=date_pattern,transaction_row_pattern") as exc_info:
        parse_pdf_transactions(b"%PDF synthetic")

    assert calls["ocr"] == 0
    assert getattr(exc_info.value, "_parse_observability", {}).get("ocr_retry_skipped") == 1
    assert getattr(exc_info.value, "_parse_observability", {}).get("ocr_retry_skip_reason") == "native_date_pattern_only"


def test_parse_pdf_transactions_keeps_native_when_coverage_is_healthy(monkeypatch) -> None:
    native_pages = ["native page 1", "native page 2", "native page 3"]
    native_result = pdf_parser_module.PdfParseResult(
        transactions=[
            pdf_parser_module.NormalizedTransaction(date="2024-03-01", description="A", amount=10.0, type="inflow"),
            pdf_parser_module.NormalizedTransaction(date="2024-03-02", description="B", amount=-5.0, type="outflow"),
            pdf_parser_module.NormalizedTransaction(date="2024-03-03", description="C", amount=3.0, type="inflow"),
            pdf_parser_module.NormalizedTransaction(date="2024-03-04", description="D", amount=-2.0, type="outflow"),
        ],
        layout=pdf_parser_module.PdfLayoutInference(
            layout_name="bradesco_net_empresa_extrato_mensal_por_periodo_v1",
            confidence=0.87,
            used_fallback=False,
        ),
        extracted_text="native",
        parse_metrics={"confidence_band": "medium", "balance_consistency_failed": 1},
    )

    calls = {"ocr": 0}

    def _ocr_stub(raw_bytes):
        calls["ocr"] += 1
        return ["ocr page"]

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: native_pages)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", _ocr_stub)
    monkeypatch.setattr(pdf_parser_module, "_parse_pdf_transactions_from_page_texts", lambda pages: native_result)

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.transactions == native_result.transactions
    assert result.extracted_text == native_result.extracted_text
    assert result.layout == native_result.layout
    assert result.parse_metrics.get("textract_attempted") == 0
    assert result.parse_metrics.get("native_text_detected") == 1
    assert calls["ocr"] == 0


def test_parse_pdf_transactions_uses_declarative_credit_debit_columns(monkeypatch) -> None:
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [VIACREDI_TABULAR_BALANCE_OK])
    monkeypatch.setattr(
        pdf_parser_module,
        "infer_pdf_layout",
        lambda text: pdf_parser_module.PdfLayoutInference(
            layout_name="viacredi_ailos_extrato_conta_corrente_v1",
            confidence=0.9,
            used_fallback=False,
        ),
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert result.layout.layout_name == "viacredi_ailos_extrato_conta_corrente_v1"
    assert result.transactions[0].amount == 1000.0
    assert result.transactions[0].description == "PIX RECEBIDO CLIENTE 123"
    assert result.transactions[1].amount == -12.34
    assert result.transactions[1].description == "TARIFA PACOTE SERVICOS 456"
    assert result.canonical_transactions[0].bank_name == "Viacredi"
    assert result.canonical_transactions[0].layout_name == "viacredi_ailos_extrato_conta_corrente_v1"
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 3
    assert result.canonical_transactions[0].source_parser == "tabular"
    assert result.canonical_transactions[0].external_reference_id == "123"
    assert result.canonical_transactions[0].running_balance == 1500.0
    assert result.canonical_transactions[1].external_reference_id == "456"
    assert result.canonical_transactions[1].running_balance == 1487.66
    assert result.parse_metrics["balance_consistency_checked"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_transactions_count"] == 2
    assert result.parse_metrics["canonical_with_running_balance_count"] == 2
    assert result.parse_metrics["canonical_with_external_reference_count"] == 2
    assert result.parse_metrics["canonical_warning_count"] == 0
    assert result.parse_metrics["canonical_balance_warning_count"] == 0
    assert result.parse_metrics["canonical_warning_transactions_count"] == 0
    assert result.parse_metrics["canonical_warning_types_count"] == 0
    assert result.parse_metrics["canonical_warning_types"] == ""
    assert result.parse_metrics["canonical_warning_types_list"] == ""
    assert result.parse_metrics["canonical_running_balance_coverage_rate"] == 1.0
    assert result.parse_metrics["canonical_external_reference_coverage_rate"] == 1.0
    assert result.parse_metrics["canonical_warning_transaction_rate"] == 0.0
    assert result.parse_metrics["canonical_source_parser_grouped_count"] == 0
    assert result.parse_metrics["canonical_source_parser_inline_count"] == 0
    assert result.parse_metrics["canonical_source_parser_tabular_count"] == 2
    assert result.parse_metrics["canonical_source_parser_columnar_count"] == 0
    assert result.parse_metrics["canonical_source_parser_types_count"] == 1
    assert result.parse_metrics["canonical_source_parser_types"] == "tabular"
    assert result.parse_metrics["canonical_source_parser_types_list"] == "tabular"


def test_parse_pdf_transactions_marks_balance_consistency_warning(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [VIACREDI_TABULAR_BALANCE_FAIL]
    )
    monkeypatch.setattr(
        pdf_parser_module,
        "infer_pdf_layout",
        lambda text: pdf_parser_module.PdfLayoutInference(
            layout_name="viacredi_ailos_extrato_conta_corrente_v1",
            confidence=0.9,
            used_fallback=False,
        ),
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.canonical_transactions) == 2
    assert result.parse_metrics["balance_consistency_checked"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 1
    assert "balance_consistency_failed" in result.canonical_transactions[1].warnings
    assert result.parse_metrics["canonical_warning_count"] == 1
    assert result.parse_metrics["canonical_balance_warning_count"] == 1
    assert result.parse_metrics["canonical_warning_transactions_count"] == 1
    assert result.parse_metrics["canonical_warning_types_count"] == 1
    assert result.parse_metrics["canonical_warning_types"] == "balance_consistency_failed"
    assert result.parse_metrics["canonical_warning_types_list"] == "balance_consistency_failed"
    assert result.parse_metrics["canonical_running_balance_coverage_rate"] == 1.0
    assert result.parse_metrics["canonical_external_reference_coverage_rate"] == 1.0
    assert result.parse_metrics["canonical_warning_transaction_rate"] == 0.5
    assert result.parse_metrics["canonical_source_parser_grouped_count"] == 0
    assert result.parse_metrics["canonical_source_parser_inline_count"] == 0
    assert result.parse_metrics["canonical_source_parser_tabular_count"] == 2
    assert result.parse_metrics["canonical_source_parser_columnar_count"] == 0
    assert result.parse_metrics["canonical_source_parser_types_count"] == 1
    assert result.parse_metrics["canonical_source_parser_types"] == "tabular"
    assert result.parse_metrics["canonical_source_parser_types_list"] == "tabular"


def test_parse_pdf_transactions_adjusts_year_rollover_from_december_to_january(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "EXTRATO PERIODO 20/12/2025 A 10/01/2026",
            "31/12 Compra mercado 10,00",
            "02/01 PIX recebido 20,00",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "inline"
    assert len(result.transactions) == 2
    assert result.transactions[0].date == "2025-12-31"
    assert result.transactions[0].amount == -10.0
    assert result.transactions[1].date == "2026-01-02"
    assert result.transactions[1].amount == 20.0


def test_parse_pdf_transactions_preserves_explicit_negative_amount_despite_credit_hint(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "12/06 AJUSTE CONTABIL CREDITO MANUAL -R$ 45,00",
            "13/06 ESTORNO TARIFA PACOTE R$ 45,00",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "inline"
    assert len(result.transactions) == 2
    assert result.transactions[0].date == "2026-06-12"
    assert result.transactions[0].amount == -45.0
    assert result.transactions[0].type == "outflow"
    assert result.transactions[1].date == "2026-06-13"
    assert result.transactions[1].amount == 45.0
    assert result.transactions[1].type == "inflow"


def test_parse_pdf_transactions_supports_grouped_slash_dates_and_credit_debit_suffix(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "Lançamentos",
            "01/04/2024",
            "PIX RECEBIDO",
            "212,05 C",
            "02/04/2024",
            "PAGAMENTO BOLETO",
            "2.150,00 D",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 2
    assert result.transactions[0].date == "2024-04-01"
    assert result.transactions[0].amount == 212.05
    assert result.transactions[1].date == "2024-04-02"
    assert result.transactions[1].amount == -2150.0


def test_parse_pdf_transactions_supports_santander_grouped_period_with_weekday_headers(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "Santander",
            "Internet Banking Empresarial",
            "Agência:",
            "Conta:",
            "Banco Santander Pessoa Jurídica",
            "Busque por um período",
            "Período",
            "01/02/2022",
            "-",
            "28/02/2022",
            "Exibindo resultados para 01/02/2022 à 28/02/2022",
            "Para consultas acima de 90 dias clique aqui.",
            "Todos",
            "Créditos",
            "Débitos",
            "Quarta, 02 de fevereiro de 2022",
            "PIX ENVIADO OUTRA",
            "DEBITO",
            "-R$ 8.000,00",
            "PAGAMENTO CONTA LUZ",
            "DEBITO",
            "-R$ 355,94",
            "TED RECEBIDA DIF TITULARIDADE STR",
            "CREDITO",
            "R$ 9.325,90",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "santander_internet_banking_empresarial_periodo_agrupado_v1"
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 3
    assert result.transactions[0].date == "2022-02-02"
    assert result.transactions[0].description == "PIX ENVIADO OUTRA"
    assert result.transactions[0].amount == -8000.0
    assert result.transactions[1].description == "PAGAMENTO CONTA LUZ"
    assert result.transactions[1].amount == -355.94
    assert result.transactions[2].description == "TED RECEBIDA DIF TITULARIDADE STR"
    assert result.transactions[2].amount == 9325.9


def test_parse_pdf_transactions_keeps_vangogh_grouped_multiline_descriptions_with_the_correct_rows() -> None:
    text = """
    Santander Van Gogh EXTRATO CONSOLIDADO
    Resumo - agosto/2024
    Nome
    Agencia
    Conta Corrente
    (-) Saldo de Conta Corrente em 31/07
    2.071,46
    (=) Saldo de Conta Corrente em 31/08
    4.174,56
    Conta Corrente
    Movimentacao
    Data
    Descricao
    N Documento
    Movimento (R$)
    Saldo (R$)
    01/08
    SALDO EM 31/07
    2.071,46
    01/08
    PIX RECEBIDO
    -
    8.736,70
    RG FAMILY OFFICE ASSESSOR
    PAGAMENTO DE BOLETO OUTROS
    -
    3.301,28-
    TITU ADMINISTRADORA DE
    IOF IMPOSTO OF 3070/24
    1,27-
    IOF ADICIONAL - AUTOMATICO
    -
    14,81-
    7.490,80
    PERIODO 01/07 A 31/07/24
    06/08
    PAGAMENTO DE BOLETO
    -
    3.123,64-
    GPROA LTDA
    REMUNERACAO APLICACAO
    -
    0,02
    4.367,18
    07/08
    PAGAMENTO DE BOLETO
    -
    192,62-
    4.174,56
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.layout.layout_name == "santander_vangogh_resumo_consolidado_conta_corrente_v1"
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert result.transactions == [
        pdf_parser_module.NormalizedTransaction(
            date="2024-08-01",
            description="SALDO ANTERIOR",
            amount=2071.46,
            type="inflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2024-08-01",
            description="PIX RECEBIDO RG FAMILY OFFICE ASSESSOR",
            amount=8736.7,
            type="inflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2024-08-01",
            description="PAGAMENTO DE BOLETO OUTROS TITU ADMINISTRADORA DE",
            amount=-3301.28,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2024-08-01",
            description="IOF IMPOSTO OF 3070/24",
            amount=-1.27,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2024-08-01",
            description="IOF ADICIONAL - AUTOMATICO PERIODO 01/07 A 31/07/24",
            amount=-14.81,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2024-08-06",
            description="PAGAMENTO DE BOLETO GPROA LTDA",
            amount=-3123.64,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2024-08-06",
            description="REMUNERACAO APLICACAO",
            amount=0.02,
            type="inflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2024-08-07",
            description="PAGAMENTO DE BOLETO",
            amount=-192.62,
            type="outflow",
        ),
    ]
    assert result.canonical_transactions[4].running_balance == 7490.8
    assert result.canonical_transactions[6].running_balance == 4367.18
    assert result.canonical_transactions[7].running_balance == 4174.56


def test_parse_pdf_transactions_parses_santander_credit_card_invoice_sections_without_mixing_headers() -> None:
    text = """
    Data: 05/01/2026
    Detalhamento da Fatura
    Pagamento e Demais Creditos
    Compra
    Data
    Descricao
    Parcela
    R$
    US$
    02/01
    PAGAMENTO DE FATURA-INTERNET
    -20.000,00
    Parcelamentos
    Compra
    Data
    Descricao
    Parcela
    R$
    US$
    )))
    13/11
    .
    MEDICAMENTOS
    03/04
    852,50
    )))
    05/01
    .
    DROGASIL
    01/02
    210,38
    Despesas
    Compra
    Data
    Descricao
    Parcela
    R$
    US$
    )))
    23/12
    SUPERMERCADO
    112,76
    )))
    02/01
    SAM
    1.580,37
    """

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([text])

    assert result.layout.layout_name == "santander_cartao_credito_detalhamento_fatura_paisagem_v1"
    assert result.parse_metrics["selected_parser"] == "sectioned_credit_card_invoice"
    assert result.transactions == [
        pdf_parser_module.NormalizedTransaction(
            date="2026-01-02",
            description="PAGAMENTO DE FATURA-INTERNET",
            amount=20000.0,
            type="inflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2025-11-13",
            description="MEDICAMENTOS PARCELA 03/04",
            amount=-852.5,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2026-01-05",
            description="DROGASIL PARCELA 01/02",
            amount=-210.38,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2025-12-23",
            description="SUPERMERCADO",
            amount=-112.76,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2026-01-02",
            description="SAM",
            amount=-1580.37,
            type="outflow",
        ),
    ]
    assert all("Compra Data" not in item.description for item in result.transactions)
    assert all(")))" not in item.description for item in result.transactions)


def test_parse_pdf_transactions_prefers_layout_text_for_sicredi_matricial_paisagem(monkeypatch) -> None:
    native_text = """
    ======================================================================================================================
    COOP CRED, POUP E INV VALOR SUSTENTAVEL EXTRATO DE CONTA CORRENTE
    JARDIM MEDICA LTDA
    19565-0
    PERIODO: DE 01/2021 A 12/2021
    DATA DOCUMENTO HISTORICO
    DEBITO
    CREDITO
    SALDO
    **/**/****
    ********
    S A L D O A N T E R I O R
    73.997,11
    04/01/2021
    174611538
    SICREDI CREDITO ELO
    91,05
    04/01/2021
    LIQUIDACAO BOLETO
    1.678,83
    04/01/2021
    REP029
    JUROS UTILIZ.CH.ESPECIAL
    59,00
    71.304,94
    """
    layout_text = """
    ======================================================================================================================
                                           COOP CRED, POUP E INV VALOR SUSTENTAVEL  EXTRATO DE CONTA CORRENTE
    JARDIM MEDICA LTDA                                                                                             19565-0
    PERIODO: DE 01/2021 A 12/2021
    DATA    DOCUMENTO  HISTORICO                                                           DEBITO      CREDITO        SALDO
    **/**/****            ********         S A L D O  A N T E R I O R                                          73.997,11
    04/01/2021            174611538        SICREDI CREDITO ELO                                                     91,05
    04/01/2021                             LIQUIDACAO BOLETO                                      1.678,83
    04/01/2021            REP029           JUROS UTILIZ.CH.ESPECIAL                               59,00          71.304,94
    """

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [layout_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "sicredi_matricial_paisagem_conta_corrente_v1"
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert [transaction.amount for transaction in result.transactions] == [91.05, -1678.83, -59.0]
    assert [transaction.type for transaction in result.transactions] == ["inflow", "outflow", "outflow"]
    assert result.canonical_transactions[2].running_balance == 71304.94


def test_parse_pdf_transactions_supports_stone_a4_statement_with_entry_exit_type_prefixes(monkeypatch) -> None:
    native_text = """
    Extrato de conta corrente
    Emitido em 04 novembro 2025 às 15:21:19
    stone
    Página 1 de 32
    Dados da conta
    Nome
    Documento
    Instituição
    Agência
    Conta
    Stone Instituição de Pagamento S.A.
    Período: de 01/09/2025 a 04/11/2025
    DATA
    TIPO
    DESCRIÇÃO
    VALOR
    SALDO
    CONTRAPARTE
    04/11/25
    Saída
    ATACADO
    Pagamento
    - R$ 3.898,12
    R$ 0,00
    04/11/25
    Entrada
    Transferência | Pix
    R$ 673,87
    R$ 3.898,12
    04/11/25
    Saída
    Transferência | Pix
    - R$ 10,00
    R$ 3.224,25
    04/11/25
    Entrada
    Recebimento vendas
    Elo | Débito
    R$ 166,47
    R$ 3.234,25
    04/11/25
    Entrada
    Recebimento vendas
    Maestro | Débito
    R$ 1.424,58
    R$ 3.067,78
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "stone_extrato_conta_corrente_a4_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert [transaction.amount for transaction in result.transactions] == [-3898.12, 673.87, -10.0, 166.47, 1424.58]
    assert result.parse_metrics["balance_consistency_checked"] == 4
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.transactions[4].description == "Entrada Recebimento vendas Maestro | Débito"
    assert result.transactions[4].type == "inflow"


def test_parse_pdf_transactions_supports_stone_grouped_lancamento_valor_saldo_statement(monkeypatch) -> None:
    native_text = """
    Extrato de conta corrente
    stone
    Titular
    Instituicao
    Stone Instituicao de Pagamento S.A.
    Documento
    Periodo
    DATA
    TIPO
    LANCAMENTO
    VALOR (R$)
    SALDO (R$)
    CONTRAPARTE
    31/12/2021
    Debito
    CENTRAL PLAST
    Compra com cartao
    Stone
    478,26
    63.173,95
    31/12/2021
    Debito
    ATACADAO 217 AS
    Compra com cartao
    Stone
    681,76
    63.652,21
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "stone_extrato_conta_corrente_lancamento_valor_saldo_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert [transaction.amount for transaction in result.transactions] == [-478.26, -681.76]
    assert result.transactions[0].description == "Debito CENTRAL PLAST Compra com cartao Stone"
    assert result.transactions[0].type == "outflow"


def test_parse_pdf_transactions_supports_bradesco_unificado_poupanca_movimentacao_section(monkeypatch) -> None:
    native_text = """
    Bradesco
    Extrato Unificado - Pessoa Juridica
    Invest Facil Bradesco
    Poupanca Facil
    Demonstrativo de Saldos e Rendimentos - Depositos a partir de 04/05/2012
    Em 30/11/2023
    Base para Calculo dos Rendimentos
    01/11
    600,00
    0,00
    0,0000%
    0,00
    04/11
    1.496,54
    1.487,52
    0,6058%
    9,02
    Total
    14.365,69
    Demonstrativo da Movimentacao
    Data
    Historico
    Documento
    Indices
    Credito
    Debito
    Saldo
    30/10
    Saldo Anterior
    8.847,52
    06/11
    Rendimentos
    0406058
    9,02
    Poup Facil-depos A Partir 4/5/12
    8.856,54
    09/11
    Rendimentos
    0906087
    41,15
    Poup Facil-depos A Partir 4/5/12
    8.897,69
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "bradesco_extrato_unificado_pj_poupanca_facil_a4_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "layout_specific_bradesco_unificado_movimentacao"
    assert [transaction.date for transaction in result.transactions] == ["2023-10-30", "2023-11-06", "2023-11-09"]
    assert [transaction.amount for transaction in result.transactions] == [8847.52, 9.02, 41.15]
    assert result.transactions[0].description == "SALDO ANTERIOR"
    assert result.transactions[1].description == "Rendimentos Poup Facil-depos A Partir 4/5/12 0406058"
    assert result.transactions[2].description == "Rendimentos Poup Facil-depos A Partir 4/5/12 0906087"
    assert result.canonical_transactions[1].running_balance == 8856.54
    assert result.canonical_transactions[2].running_balance == 8897.69


def test_parse_pdf_transactions_prefers_neon_mei_facil_layout_text_profile(monkeypatch) -> None:
    native_text = """
    BANCO MEI FACIL
    Atualizacao:
    Nome:
    Banco 536
    Neon pagamentos IP
    Agencia:
    Conta:
    Lancamentos
    data
    lancamento
    valor (R$)
    saldos (R$)
    lancamentos
    01/01/2024
    SALDO ANTERIOR
    1.902,45
    02/01/2024
    PAGAMENTO FATURA CARTAO CRED
    (453,02)
    1.449,43
    02/01/2024
    PIX ENVIADO PARA ANGELO
    (1.440,00)
    9,43
    08/01/2024
    PIX RECEBIDO AILTON AUGUSTO
    350,00
    359,43
    08/01/2024
    PIX RECEBIDO RODRIGO
    450,00
    809,43
    08/01/2024
    PIX ENVIADO PARA ANGELO
    (260,00)
    549,43
    09/01/2024
    PIX ENVIADO PARA ASAAS
    (200,00)
    349,43
    10/01/2024
    PIX RECEBIDO LUIZ GUSTAVO
    700,00
    1.049,43
    10/01/2024
    PIX RECEBIDO MONIKY
    350,00
    1.399,43
    10/01/2024
    PIX ENVIADO ANGELO
    (1.399,43)
    0,00
    --
    0,00
    31/01/2024
    SALDO TOTAL DISPONIVEL DIA
    0,00
    0,00
    """
    layout_text = "\n".join(
        [
            "BANCO MEI FACIL",
            "Atualizacao:",
            "Nome:",
            "",
            "Banco 536          Neon pagamentos IP",
            "Agencia:",
            "Conta:",
            "Lancamentos",
            "",
            (
                " data                       lancamento"
                "                                                            valor (R$)            saldos (R$)"
            ),
            " lancamentos",
            "",
            (
                " 01/01/2024                 SALDO ANTERIOR"
                "                                                                                        1.902,45"
            ),
            (
                " 02/01/2024                 PAGAMENTO FATURA CARTAO CRED"
                "                                                    (453,02)              1.449,43"
            ),
            (
                " 02/01/2024                 PIX ENVIADO PARA ANGELO"
                "                                                       (1.440,00)                  9,43"
            ),
            (
                " 08/01/2024                 PIX RECEBIDO AILTON AUGUSTO"
                "                                                      350,00                 359,43"
            ),
            (
                " 08/01/2024                 PIX RECEBIDO RODRIGO"
                "                                                             450,00                 809,43"
            ),
            (
                " 08/01/2024                 PIX ENVIADO PARA ANGELO"
                "                                                         (260,00)                549,43"
            ),
            (
                " 09/01/2024                 PIX ENVIADO PARA ASAAS"
                "                                                          (200,00)                349,43"
            ),
            (
                " 10/01/2024                 PIX RECEBIDO LUIZ GUSTAVO"
                "                                                        700,00               1.049,43"
            ),
            (
                " 10/01/2024                 PIX RECEBIDO MONIKY"
                "                                                              350,00               1.399,43"
            ),
            (
                " 10/01/2024                 PIX ENVIADO ANGELO"
                "                                                            (1.399,43)                  0,00"
            ),
            (
                "                                                                                                                   "
                "--                 0,00"
            ),
            "",
            (
                " 31/01/2024                 SALDO TOTAL DISPONIVEL DIA"
                "                                                          0,00                  0,00"
            ),
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [layout_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "neon_banco_mei_facil_extrato_a4_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert [transaction.amount for transaction in result.transactions] == [
        1902.45,
        -453.02,
        -1440.0,
        350.0,
        450.0,
        -260.0,
        -200.0,
        700.0,
        350.0,
        -1399.43,
    ]
    assert result.transactions[0].description == "SALDO ANTERIOR"
    assert result.transactions[-1].description == "PIX ENVIADO ANGELO"
    assert result.canonical_transactions[0].running_balance == 1902.45
    assert result.canonical_transactions[-1].running_balance == 0.0


def test_parse_pdf_transactions_supports_banco_do_nordeste_extrato_consolidado_statement(monkeypatch) -> None:
    native_text = """
    Banco do
    Nordeste
    EXTRATO CONSOLIDADO
    Informacoes Gerais
    Titular:
    Mes:
    Marco/2023
    CNPJ:
    Data de Emissao:
    Detalhamento do Extrato
    REFERENCIA: MARCO/2023
    < RESUMO DAS MOVIMENTACOES NO PERIODO >
    > CONTA CORRENTE
    AGENCIA:
      SALDO CONTA CORRENTE........................
    2.001,45
      SALDO POUPANCA CORRENTE ATE 03/05/2012......
    0,00
      SALDO POUPANCA CORRENTE A PARTIR DE 04/05/2012....
    0,00
      SALDO BLOQUEADO.............................
    0,00
      SALDO ATUAL.................................
    2.001,45
      SALDO MEDIO DE CONTA CORRENTE...............
    21.561,56
      SALDO MEDIO DE POUPANCA CORRENTE............
    0,00
    > DEMONSTRATIVO DA MOVIMENTACAO DE CONTA CORRENTE
    DIA
    HISTORICO
    DOCUMENTO
    VALOR
    SALDO
    1
    SALDO ANTERIOR
    0,00
    69,86
    1
    TARIFA MANUTENCAO CONTA
    474
    53,00-
    16,86
    3
    ESTORNO DE TARIFAS
    996
    2.600,00+
    2.616,86
    7
    TARIFA CARTAO
    996
    792,75-
    1.824,11
    9
    CREDITO TED
    104
    177,34+
    2.001,45
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "banco_do_nordeste_extrato_consolidado_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "layout_specific_banco_nordeste_consolidado"
    assert [transaction.date for transaction in result.transactions] == [
        "2023-03-01",
        "2023-03-01",
        "2023-03-03",
        "2023-03-07",
        "2023-03-09",
    ]
    assert [transaction.amount for transaction in result.transactions] == [69.86, -53.0, 2600.0, -792.75, 177.34]
    assert result.transactions[0].description == "SALDO ANTERIOR"
    assert result.transactions[1].description == "TARIFA MANUTENCAO CONTA 474"
    assert result.transactions[2].description == "ESTORNO DE TARIFAS 996"
    assert result.transactions[4].description == "CREDITO TED 104"
    assert [item.running_balance for item in result.canonical_transactions] == [69.86, 16.86, 2616.86, 1824.11, 2001.45]


def test_parse_pdf_transactions_supports_banco_do_nordeste_fundos_rentabilidade_statement(monkeypatch) -> None:
    native_text = """
    FUNDOS DE INVESTIMENTOS - RENTABILIDADE ( % )
    ADMINISTRADOR FIDUCIARIO:
    PRODUTO
    REND. MENSAL
    REND. ANUAL
    ULT. 12 MESES
    BNB AUTOMATICO FI RF CURTO PRAZO
    0,6345
    1,4101
    10,1181
    MOVIMENTACOES BNB AUTOMATICO
    DIA
    HISTORICO
    QUANT. COTAS
    VALOR COTA
    VALOR EM R$
    01
    SALDO INICIAL
    4.983,898
    11,448227
    57.056,80
    03
    APLICACAO
    5.617,694
    11,452214
    64.335,03
    05
    APLICACAO
    380,345
    11,456059
    4.357,25
    09
    APLICACAO
    3.288,032
    11,459957
    37.680,71
    15
    RESGATE
    5.592,692
    11,463803
    64.113,52
    15
    I.O.F.  S/RESGATE
    96,44
    15
    I.R. FEDERAL
    11,34
    22
    RESGATE
    1.052,419
    11,467650
    12.068,77
    22
    I.O.F.  S/RESGATE
    12,99
    22
    I.R. FEDERAL
    0,72
    27
    APLICACAO
    4.288,512
    11,471437
    49.195,40
    29
    RESGATE
    4.517,971
    11,475314
    51.845,14
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module.text_extraction, "read_pdf_creation_month_year", lambda raw_bytes: (4, 2021))

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "banco_do_nordeste_fundos_investimentos_rentabilidade_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "layout_specific_banco_nordeste_fundos_rentabilidade"
    assert [transaction.date for transaction in result.transactions] == [
        "2021-04-01",
        "2021-04-03",
        "2021-04-05",
        "2021-04-09",
        "2021-04-15",
        "2021-04-15",
        "2021-04-15",
        "2021-04-22",
        "2021-04-22",
        "2021-04-22",
        "2021-04-27",
        "2021-04-29",
    ]
    assert [transaction.amount for transaction in result.transactions] == [
        57056.8,
        -64335.03,
        -4357.25,
        -37680.71,
        64113.52,
        -96.44,
        -11.34,
        12068.77,
        -12.99,
        -0.72,
        -49195.4,
        51845.14,
    ]
    assert result.transactions[0].description == "SALDO INICIAL"
    assert result.transactions[5].description == "I.O.F. S/RESGATE"
    assert result.transactions[6].description == "I.R. FEDERAL"


def test_parse_pdf_transactions_resolves_singular_credit_debit_headers_in_tabular_positions() -> None:
    sample_text = "\n".join(
        [
            "COOP CRED, POUP E INV VALOR SUSTENTAVEL EXTRATO DE CONTA CORRENTE",
            "PERIODO: DE 01/2021 A 12/2021",
            "DATA    DOCUMENTO  HISTORICO                                                           DEBITO      CREDITO        SALDO",
            "04/01/2021            174611538        SICREDI CREDITO ELO                                                     91,05",
            "04/01/2021                             LIQUIDACAO BOLETO                                      1.678,83",
            "04/01/2021            REP029           JUROS UTILIZ.CH.ESPECIAL                               59,00          71.304,94",
        ]
    )
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts([sample_text], preserve_layout_spacing=True)

    assert result.layout.layout_name == "sicredi_matricial_paisagem_conta_corrente_v1"
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert [transaction.amount for transaction in result.transactions] == [91.05, -1678.83, -59.0]


def test_parse_pdf_transactions_uses_running_balance_to_override_heuristic_when_amount_has_no_explicit_sign(
    monkeypatch,
) -> None:
    sample_text = "\n".join(
        [
            "Data",
            "Historico",
            "Documento",
            "Valor",
            "Saldo",
            "01/04/2024",
            "SALDO ANTERIOR",
            "1.000,00",
            "02/04/2024",
            "PAGAMENTO CARTAO",
            "999999",
            "125,45",
            "1.125,45",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 2
    assert result.transactions[0].description == "SALDO ANTERIOR"
    assert result.transactions[0].amount == 1000.0
    assert result.transactions[1].description == "PAGAMENTO CARTAO 999999"
    assert result.transactions[1].amount == 125.45


def test_parse_pdf_transactions_keeps_explicit_negative_sign_even_if_running_balance_suggests_positive(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "Data",
            "Historico",
            "Documento",
            "Valor",
            "Saldo",
            "01/04/2024",
            "SALDO ANTERIOR",
            "1.000,00",
            "02/04/2024",
            "PAGAMENTO CARTAO",
            "999999",
            "-125,45",
            "1.125,45",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 2
    assert result.transactions[0].description == "SALDO ANTERIOR"
    assert result.transactions[1].amount == -125.45


def test_parse_pdf_transactions_does_not_reconcile_grouped_sign_from_non_adjacent_running_balance(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "01. Conta Corrente e Aplicações Automáticas",
            "Conta Corrente |",
            "Movimentação",
            "data",
            "descrição",
            "entradas R$",
            "(créditos)",
            "saídas R$",
            "(débitos)",
            "saldo R$",
            "27/10",
            "Saldo anterior",
            "3.039,28",
            "03/11",
            "Sitpag",
            "3.000,00-",
            "Tar Contr",
            "165,00-",
            "Tar Conta Certa",
            "201,90-",
            "Res Aplic Aut Mais",
            "3.029,28",
            "Rend Pag Aplic Aut Mais",
            "0,02",
            "2.701,68",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 6
    assert result.transactions[-1].description == "Rend Pag Aplic Aut Mais"
    assert result.transactions[-1].amount == 0.02
    assert result.canonical_transactions[-1].running_balance == 2701.68


def test_parse_pdf_transactions_ignores_grouped_saldo_rows_from_transaction_totals(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "01/04/2024",
            "SALDO ANTERIOR",
            "1.000,00",
            "02/04/2024",
            "PIX RECEBIDO",
            "200,00 C",
            "02/04/2024",
            "SALDO",
            "1.200,00",
            "03/04/2024",
            "PAGAMENTO BOLETO",
            "50,00 D",
            "03/04/2024",
            "SALDO FINAL",
            "1.150,00",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 3
    assert result.transactions[0].description == "SALDO ANTERIOR"
    assert result.transactions[0].amount == 1000.0
    assert result.transactions[1].description == "PIX RECEBIDO"
    assert result.transactions[1].amount == 200.0
    assert result.transactions[2].description == "PAGAMENTO BOLETO"
    assert result.transactions[2].amount == -50.0


def test_parse_pdf_transactions_ignores_caixa_siatr_saldo_dia_rows(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "SIATR-SISTEMA DE AUTO ATENDIMENTO REESTRUTURADO",
            "SALDOS E LANCAMENTOS",
            "CAIXA",
            "SDO DISP:",
            "5.740,61C",
            "DATA MOV NR.DOC DESCRICAO",
            "VALOR",
            "SALDO",
            "07/08/25",
            "071551 CRED PIX CHAVE",
            "5.000,00C",
            "7.446,29C",
            "07/08/25",
            "071553 PAG BOLETO IBC",
            "6.328,90D",
            "1.117,39C",
            "07/08/25",
            "000000 SALDO DIA",
            "0,00C",
            "1.117,39C",
            "11/08/25",
            "101833 CRED PIX CHAVE",
            "5.000,00C",
            "5.879,54C",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "caixa_siatr_saldos_lancamentos_a4_v1"
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 3
    assert [item.description for item in result.transactions] == [
        "071551 CRED PIX CHAVE",
        "071553 PAG BOLETO IBC",
        "101833 CRED PIX CHAVE",
    ]
    assert all("SALDO DIA" not in item.description for item in result.transactions)


def test_parse_pdf_transactions_does_not_attach_total_disponivel_header_as_running_balance(monkeypatch) -> None:
    page_one = "\n".join(
        [
            "01/01/2024",
            "PIX RECEBIDO CLIENTE ALFA",
            "100,00",
            "1.000,00",
        ]
    )
    page_two = "\n".join(
        [
            "bradesco",
            "Total Disponível (R$)",
            "-1.320.888,92",
            "-1.320.888,92",
            "02/01/2024",
            "PAGAMENTO BOLETO",
            "50,00 D",
            "950,00",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [page_one, page_two])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
    assert result.transactions[0].description == "PIX RECEBIDO CLIENTE ALFA"
    assert result.transactions[1].description == "PAGAMENTO BOLETO"
    assert all(
        item.running_balance is None or abs(abs(item.running_balance) - 1320888.92) > 0.01
        for item in (result.canonical_transactions or [])
    )


def test_parse_pdf_transactions_includes_opening_balance_without_date_on_first_transaction_date(monkeypatch) -> None:
    sample_text = "\n".join(
        [
            "Lançamentos",
            "Saldo Anterior",
            "25.430,25 +",
            "02/01/2025",
            "CRÉDITO PIX",
            "000123",
            "8.450,00",
            "33.880,25 +",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 2
    assert result.transactions[0].date == "2025-01-02"
    assert result.transactions[0].description == "SALDO ANTERIOR"
    assert result.transactions[0].amount == 25430.25
    assert result.transactions[1].description == "CRÉDITO PIX 000123"
    assert result.transactions[1].amount == 8450.0


def test_parse_pdf_transactions_supports_santander_mobile_grouped_dates_and_inline_transaction_date_details(
    monkeypatch,
) -> None:
    sample_text = "\n".join(
        [
            "Santander",
            "Internet Banking Empresarial",
            "Agência:",
            "Conta:",
            "17 de outubro de 2023, terça-feira",
            "Saldo do dia",
            "R$ 0,00",
            "↓",
            "Resgate",
            "R$ 12,00",
            "↑",
            "Tarifa ted",
            "16/10/2023",
            "- R$ 12,00",
            "16 de outubro de 2023, segunda-feira",
            "Saldo do dia",
            "R$ 0,00",
            "↑",
            "Aplicacao",
            "- R$ 25.000,00",
            "↑",
            "Ted enviada",
            "02971354000109",
            "- R$ 26.000,00",
            "↑",
            "Transf valor p/ conta dif titular",
            "304",
            "- R$ 49.000,00",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [sample_text])
    monkeypatch.setattr(
        pdf_parser_module,
        "infer_pdf_layout",
        lambda text: pdf_parser_module.PdfLayoutInference(
            layout_name="santander_empresarial_extrato_365_dias_mobile_grouped_v1",
            confidence=0.87,
            used_fallback=False,
        ),
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "santander_empresarial_extrato_365_dias_mobile_grouped_v1"
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert result.transactions == [
        pdf_parser_module.NormalizedTransaction(
            date="2023-10-17",
            description="Resgate",
            amount=12.0,
            type="inflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2023-10-17",
            description="Tarifa ted 16/10/2023",
            amount=-12.0,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2023-10-16",
            description="Aplicacao",
            amount=-25000.0,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2023-10-16",
            description="Ted enviada 02971354000109",
            amount=-26000.0,
            type="outflow",
        ),
        pdf_parser_module.NormalizedTransaction(
            date="2023-10-16",
            description="Transf valor p/ conta dif titular 304",
            amount=-49000.0,
            type="outflow",
        ),
    ]


def test_parse_columnar_statement_blocks_skips_incomplete_rows_and_parses_valid_block(monkeypatch) -> None:
    lines = [
        pdf_parser_module._PdfLine(text="15/04", page_number=1, line_number=1),
        pdf_parser_module._PdfLine(text="16/04", page_number=1, line_number=2),
        pdf_parser_module._PdfLine(text="Pagamento", page_number=1, line_number=3),
        pdf_parser_module._PdfLine(text="DEBITO", page_number=1, line_number=4),
        pdf_parser_module._PdfLine(text="10,00", page_number=1, line_number=5),
    ]

    monkeypatch.setattr(pdf_parser_module, "next_columnar_block_index", lambda all_texts, current_index: 5)

    parsed_rows, candidates = pdf_parser_module._parse_columnar_statement_blocks(lines)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2026-04-16"
    assert parsed_rows[0].transaction.description == "Pagamento"
    assert parsed_rows[0].transaction.amount == -10.0
    assert parsed_rows[0].source_page == 1
    assert parsed_rows[0].source_line == 2


def test_build_grouped_amount_only_transaction_returns_none_without_description_parts() -> None:
    line = pdf_parser_module._PdfLine(text="10,00", page_number=1, line_number=5)

    parsed_row = pdf_parser_module._build_grouped_amount_only_transaction(
        date="2026-04-16",
        description_parts=[],
        line=line,
        section_hint="debit",
    )

    assert parsed_row is None


def test_build_grouped_amount_only_transaction_builds_transaction_with_description() -> None:
    line = pdf_parser_module._PdfLine(text="10,00", page_number=1, line_number=5)

    parsed_row = pdf_parser_module._build_grouped_amount_only_transaction(
        date="2026-04-16",
        description_parts=["Pagamento PIX"],
        line=line,
        section_hint="debit",
    )

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-16"
    assert parsed_row.transaction.description == "Pagamento PIX"
    assert parsed_row.transaction.amount == -10.0
    assert parsed_row.source_page == 1
    assert parsed_row.source_line == 5


def test_parse_grouped_date_line_state_resets_parts_and_updates_hint() -> None:
    line = pdf_parser_module._PdfLine(text="16/04 PIX RECEBIDO 10,00", page_number=1, line_number=2)

    next_date, next_section_hint, next_description_parts, inline_transaction = pdf_parser_module._parse_grouped_date_line_state(
        line=line,
        grouped_date="2026-04-16",
        grouped_rest="PIX RECEBIDO 10,00",
    )

    assert next_date == "2026-04-16"
    assert next_section_hint is None
    assert next_description_parts == []
    assert inline_transaction is not None
    assert inline_transaction.transaction.description == "PIX RECEBIDO"
    assert inline_transaction.transaction.amount == 10.0


def test_parse_inline_statement_line_returns_none_for_non_matching_line() -> None:
    line = pdf_parser_module._PdfLine(text="SALDO ANTERIOR 1.234,56", page_number=1, line_number=1)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is None


def test_parse_inline_statement_line_builds_transaction_for_valid_line() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00", page_number=2, line_number=7)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO"
    assert parsed_row.transaction.amount == 25.0
    assert parsed_row.source_page == 2
    assert parsed_row.source_line == 7


def test_parse_inline_statement_line_accepts_trailing_ocr_noise_after_amount() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00 |", page_number=2, line_number=8)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO"
    assert parsed_row.transaction.amount == 25.0
    assert parsed_row.source_page == 2
    assert parsed_row.source_line == 8


def test_parse_inline_statement_line_accepts_trailing_ocr_noise_glyph_after_amount() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00 I", page_number=2, line_number=9)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO"
    assert parsed_row.transaction.amount == 25.0
    assert parsed_row.source_page == 2
    assert parsed_row.source_line == 9


def test_parse_inline_statement_line_rejects_trailing_non_noise_text_after_amount() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00 DOC", page_number=2, line_number=10)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is None


def test_parse_inline_statement_rows_parses_split_amount_on_next_line() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PIX RECEBIDO CLIENTE ACME", page_number=1, line_number=4),
        pdf_parser_module._PdfLine(text="250,00", page_number=1, line_number=5),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2026-04-03"
    assert parsed_rows[0].transaction.description == "PIX RECEBIDO CLIENTE ACME"
    assert parsed_rows[0].transaction.amount == 250.0
    assert parsed_rows[0].source_page == 1
    assert parsed_rows[0].source_line == 4


def test_parse_inline_statement_rows_parses_multiline_description_then_amount() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=10),
        pdf_parser_module._PdfLine(text="INDUSTRIA E COMERCIO LTDA", page_number=1, line_number=11),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=12),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2026-04-03"
    assert parsed_rows[0].transaction.description == "PAGAMENTO FORNECEDOR ALFA INDUSTRIA E COMERCIO LTDA"
    assert parsed_rows[0].transaction.amount == -150.25
    assert parsed_rows[0].source_page == 1
    assert parsed_rows[0].source_line == 10


def test_parse_inline_statement_rows_ignores_ocr_noise_line_in_pending_multiline() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=60),
        pdf_parser_module._PdfLine(text="||", page_number=1, line_number=61),
        pdf_parser_module._PdfLine(text="INDUSTRIA E COMERCIO LTDA", page_number=1, line_number=62),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=63),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2026-04-03"
    assert parsed_rows[0].transaction.description == "PAGAMENTO FORNECEDOR ALFA INDUSTRIA E COMERCIO LTDA"
    assert parsed_rows[0].transaction.amount == -150.25
    assert parsed_rows[0].source_page == 1
    assert parsed_rows[0].source_line == 60


def test_parse_inline_statement_rows_cancels_pending_on_balance_line() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=20),
        pdf_parser_module._PdfLine(text="SALDO DO DIA 1.274,16", page_number=1, line_number=21),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=22),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 0
    assert parsed_rows == []


def test_parse_inline_statement_rows_cancels_pending_on_header_line() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=30),
        pdf_parser_module._PdfLine(text="EXTRATO CONTA CORRENTE - CONTINUACAO", page_number=1, line_number=31),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=32),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 0
    assert parsed_rows == []


def test_parse_inline_statement_rows_cancels_pending_on_page_change_before_amount() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=40),
        pdf_parser_module._PdfLine(text="150,25", page_number=2, line_number=1),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 0
    assert parsed_rows == []


def test_parse_inline_statement_rows_cancels_pending_on_tabular_header_before_amount() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/04 PAGAMENTO FORNECEDOR ALFA", page_number=1, line_number=50),
        pdf_parser_module._PdfLine(
            text="DATA DESCRICAO DOCUMENTO CREDITO (R$) DEBITO (R$) SALDO (R$)",
            page_number=1,
            line_number=51,
        ),
        pdf_parser_module._PdfLine(text="150,25", page_number=1, line_number=52),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 0
    assert parsed_rows == []


def test_parse_inline_statement_rows_maps_ocr_columnar_dates_to_later_amount_block() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="02/01/2025 CRÉDITO PIX - CLIENTE BETA", page_number=1, line_number=1),
        pdf_parser_module._PdfLine(text="03/01/2025 DÉBITO PIX - FORNECEDOR GAMMA", page_number=1, line_number=2),
        pdf_parser_module._PdfLine(text="DOC", page_number=1, line_number=3),
        pdf_parser_module._PdfLine(text="000123", page_number=1, line_number=4),
        pdf_parser_module._PdfLine(text="Valor", page_number=1, line_number=5),
        pdf_parser_module._PdfLine(text="8.450,00", page_number=1, line_number=6),
        pdf_parser_module._PdfLine(text="2.600,00", page_number=1, line_number=7),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 2
    assert len(parsed_rows) == 2
    assert parsed_rows[0].transaction.date == "2025-01-02"
    assert parsed_rows[0].transaction.amount == 8450.0
    assert parsed_rows[0].transaction.description == "CRÉDITO PIX - CLIENTE BETA"
    assert parsed_rows[1].transaction.date == "2025-01-03"
    assert parsed_rows[1].transaction.amount == -2600.0
    assert parsed_rows[1].transaction.description == "DÉBITO PIX - FORNECEDOR GAMMA"


def test_parse_inline_statement_rows_infers_opening_balance_from_columnar_balance_block() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="Saldo Anterior", page_number=1, line_number=1),
        pdf_parser_module._PdfLine(text="02/01/2025 CRÉDITO PIX - CLIENTE BETA", page_number=1, line_number=2),
        pdf_parser_module._PdfLine(text="03/01/2025 DÉBITO PIX - FORNECEDOR GAMMA", page_number=1, line_number=3),
        pdf_parser_module._PdfLine(text="DOC", page_number=1, line_number=4),
        pdf_parser_module._PdfLine(text="Valor", page_number=1, line_number=5),
        pdf_parser_module._PdfLine(text="8.450,00", page_number=1, line_number=6),
        pdf_parser_module._PdfLine(text="2.600,00", page_number=1, line_number=7),
        pdf_parser_module._PdfLine(text="Saldo", page_number=1, line_number=8),
        pdf_parser_module._PdfLine(text="33.880,25", page_number=1, line_number=9),
        pdf_parser_module._PdfLine(text="31.280,25", page_number=1, line_number=10),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_inline_statement_rows(lines)

    assert candidates == 2
    assert len(parsed_rows) == 3
    assert parsed_rows[0].transaction.description == "SALDO ANTERIOR"
    assert parsed_rows[0].transaction.amount == 25430.25
    assert parsed_rows[1].transaction.date == "2025-01-02"
    assert parsed_rows[2].transaction.date == "2025-01-03"


def test_parse_inline_statement_line_accepts_trailing_mixed_ocr_noise_after_amount() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 PIX RECEBIDO 25,00 ||I", page_number=2, line_number=11)

    parsed_row = pdf_parser_module._parse_inline_statement_line(line=line, inferred_year=2026)

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO"
    assert parsed_row.transaction.amount == 25.0
    assert parsed_row.source_page == 2
    assert parsed_row.source_line == 11


def test_parse_tabular_statement_line_returns_none_when_no_date_prefix() -> None:
    line = pdf_parser_module._PdfLine(text="SALDO ANTERIOR 1.000,00", page_number=1, line_number=1)

    parsed_row = pdf_parser_module._parse_tabular_statement_line(
        line=line,
        inferred_year=2026,
        tabular_profile=None,
    )

    assert parsed_row is None


def test_parse_tabular_statement_line_builds_transaction_with_metadata() -> None:
    line = pdf_parser_module._PdfLine(
        text="10/04 PIX RECEBIDO CLIENTE 123 1.000,00 1.500,00",
        page_number=3,
        line_number=12,
    )

    parsed_row = pdf_parser_module._parse_tabular_statement_line(
        line=line,
        inferred_year=2026,
        tabular_profile=None,
    )

    assert parsed_row is not None
    assert parsed_row.transaction.date == "2026-04-10"
    assert parsed_row.transaction.description == "PIX RECEBIDO CLIENTE 123"
    assert parsed_row.transaction.amount == 1000.0
    assert parsed_row.running_balance == 1500.0
    assert parsed_row.source_page == 3
    assert parsed_row.source_line == 12


def test_parse_tabular_statement_line_uses_profile_v2_ignored_rows() -> None:
    profile = pdf_parser_module.get_layout_profile("banco_do_nordeste_extrato_periodo_a4_v1")
    assert profile is not None
    line = pdf_parser_module._PdfLine(
        text="10/04/2021 Detalhamento do Extrato 1.000,00",
        page_number=1,
        line_number=12,
    )

    parsed_row, is_candidate = pdf_parser_module._classify_tabular_statement_line(
        line=line,
        inferred_year=2021,
        tabular_profile=profile,
    )

    assert is_candidate is True
    assert parsed_row is None


def test_parse_tabular_statement_rows_recovers_multiline_ocr_row() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/03/2024", page_number=1, line_number=10),
        pdf_parser_module._PdfLine(text="TARIFA PACOTE SERVICOS", page_number=1, line_number=11),
        pdf_parser_module._PdfLine(text="030324886", page_number=1, line_number=12),
        pdf_parser_module._PdfLine(text="-32,40", page_number=1, line_number=13),
        pdf_parser_module._PdfLine(text="5.908,12", page_number=1, line_number=14),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_tabular_statement_rows(lines, layout_profile=None)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2024-03-03"
    assert parsed_rows[0].transaction.description == "TARIFA PACOTE SERVICOS 030324886"
    assert parsed_rows[0].transaction.amount == -32.4
    assert parsed_rows[0].running_balance == 5908.12


def test_parse_tabular_statement_rows_recovers_multiline_ocr_row_with_header_noise() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/03/2024", page_number=1, line_number=3),
        pdf_parser_module._PdfLine(text="Extrato Mensal / Por Período", page_number=1, line_number=5),
        pdf_parser_module._PdfLine(text="Data da operação: 15/04/2024 - 09h15", page_number=1, line_number=7),
        pdf_parser_module._PdfLine(text="TARIFA PACOTE SERVICOS", page_number=1, line_number=9),
        pdf_parser_module._PdfLine(text="030324886", page_number=1, line_number=11),
        pdf_parser_module._PdfLine(text="-32,40", page_number=1, line_number=13),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_tabular_statement_rows(lines, layout_profile=None)

    assert candidates == 1
    assert len(parsed_rows) == 1
    assert parsed_rows[0].transaction.date == "2024-03-03"
    assert parsed_rows[0].transaction.description == "TARIFA PACOTE SERVICOS 030324886"
    assert parsed_rows[0].transaction.amount == -32.4


def test_parse_tabular_statement_rows_attaches_amount_only_line_as_running_balance_same_page() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="02/03/2024 TED RECEBIDA OMEGA 020324975 4.729,51 9.687,29", page_number=1, line_number=10),
        pdf_parser_module._PdfLine(text="03/03/2024 TARIFA BANCARIA 030324755 -37,91", page_number=1, line_number=11),
        pdf_parser_module._PdfLine(text="5.940,52", page_number=1, line_number=12),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_tabular_statement_rows(lines, layout_profile=None)

    assert candidates == 2
    assert len(parsed_rows) == 2
    assert parsed_rows[1].transaction.description == "TARIFA BANCARIA 030324755"
    assert parsed_rows[1].running_balance == 5940.52
    assert parsed_rows[1].transaction.amount == -37.91


def test_parse_tabular_statement_rows_ignores_pre_opening_balance_ocr_leak() -> None:
    lines = [
        pdf_parser_module._PdfLine(text="03/03/2024", page_number=1, line_number=3),
        pdf_parser_module._PdfLine(text="TARIFA PACOTE SERVICOS", page_number=1, line_number=4),
        pdf_parser_module._PdfLine(text="030324886", page_number=1, line_number=5),
        pdf_parser_module._PdfLine(text="-32,40", page_number=1, line_number=6),
        pdf_parser_module._PdfLine(text="SALDO ANTERIOR", page_number=1, line_number=20),
        pdf_parser_module._PdfLine(text="29/02/2024 SALDO ANTERIOR -441,66", page_number=1, line_number=21),
        pdf_parser_module._PdfLine(text="01/03/2024 DEPOSITO IDENTIFICADO 010324854 289,73 -151,93", page_number=1, line_number=22),
    ]

    parsed_rows, candidates = pdf_parser_module._parse_tabular_statement_rows(lines, layout_profile=None)

    assert candidates == 2
    assert len(parsed_rows) == 2
    assert parsed_rows[0].transaction.date == "2024-02-29"
    assert "030324886" not in parsed_rows[0].transaction.description


def test_update_grouped_section_state_keeps_description_when_hint_is_unchanged() -> None:
    next_hint, next_parts, should_continue = pdf_parser_module._update_grouped_section_state(
        normalized_line="COMPRA CARTAO",
        current_section_hint="debit",
        description_parts=["compra mercado"],
    )

    assert next_hint == "debit"
    assert next_parts == ["compra mercado"]
    assert should_continue is False


def test_update_grouped_section_state_resets_description_when_hint_changes() -> None:
    next_hint, next_parts, should_continue = pdf_parser_module._update_grouped_section_state(
        normalized_line="TOTAL DE ENTRADAS",
        current_section_hint="outflow",
        description_parts=["compra mercado"],
    )

    assert next_hint == "inflow"
    assert next_parts == []
    assert should_continue is True


def test_handle_grouped_ignored_line_resets_description_parts() -> None:
    next_parts, should_continue = pdf_parser_module._handle_grouped_ignored_line(
        normalized_line="SALDO INICIAL DO DIA",
        description_parts=["compra mercado"],
    )

    assert next_parts == []
    assert should_continue is True


def test_handle_grouped_ignored_line_keeps_description_when_not_ignored() -> None:
    next_parts, should_continue = pdf_parser_module._handle_grouped_ignored_line(
        normalized_line="COMPRA MERCADO",
        description_parts=["compra mercado"],
    )

    assert next_parts == ["compra mercado"]
    assert should_continue is False


def test_append_grouped_description_part_ignores_blank_text() -> None:
    parts = ["compra mercado"]

    next_parts = pdf_parser_module._append_grouped_description_part(
        description_parts=parts,
        raw_text="   ",
    )

    assert next_parts == ["compra mercado"]


def test_append_grouped_description_part_appends_cleaned_text() -> None:
    parts = ["compra mercado"]

    next_parts = pdf_parser_module._append_grouped_description_part(
        description_parts=parts,
        raw_text="  pagamento pix  ",
    )

    assert next_parts == ["compra mercado", "pagamento pix"]


def test_resolve_next_columnar_index_increments_when_row_is_none() -> None:
    next_index = pdf_parser_module._resolve_next_columnar_index(
        line_texts=["10/04", "Pagamento", "DEBITO", "10,00"],
        current_index=2,
        parsed_row=None,
    )

    assert next_index == 3


def test_resolve_next_columnar_index_uses_block_rule_when_row_exists(monkeypatch) -> None:
    line_texts = ["10/04", "Pagamento", "DEBITO", "10,00"]
    parsed_row = pdf_parser_module._ParsedTransaction(
        transaction=pdf_parser_module.NormalizedTransaction(
            date="2026-04-10",
            description="Pagamento",
            amount=-10.0,
            type="outflow",
        ),
        source_page=1,
        source_line=1,
    )
    monkeypatch.setattr(pdf_parser_module, "next_columnar_block_index", lambda lines, current_index: 99)

    next_index = pdf_parser_module._resolve_next_columnar_index(
        line_texts=line_texts,
        current_index=0,
        parsed_row=parsed_row,
    )

    assert next_index == 99


def test_build_tabular_amount_details_returns_signed_amount_and_balance() -> None:
    details = pdf_parser_module._build_tabular_amount_details(
        amount_token_value="1.000,00",
        selected_role="credit",
        raw_description="PIX RECEBIDO CLIENTE",
        balance_token_value="1.500,00",
    )

    assert details["signed_amount"] == 1000.0
    assert details["running_balance"] == 1500.0


def test_build_tabular_amount_details_handles_missing_balance() -> None:
    details = pdf_parser_module._build_tabular_amount_details(
        amount_token_value="10,00",
        selected_role="debit",
        raw_description="TARIFA PACOTE",
        balance_token_value=None,
    )

    assert details["signed_amount"] == -10.0
    assert details["running_balance"] is None


def test_classify_tabular_statement_line_marks_candidate_without_description() -> None:
    line = pdf_parser_module._PdfLine(text="10/04 1,00", page_number=1, line_number=1)

    parsed_row, is_candidate = pdf_parser_module._classify_tabular_statement_line(
        line=line,
        inferred_year=2026,
        tabular_profile=None,
    )

    assert is_candidate is True
    assert parsed_row is None


def test_handle_grouped_amount_only_line_resets_parts_when_parsed() -> None:
    line = pdf_parser_module._PdfLine(text="10,00", page_number=1, line_number=5)

    parsed_row, next_parts, should_continue = pdf_parser_module._handle_grouped_amount_only_line(
        current_date="2026-04-16",
        description_parts=["Pagamento PIX"],
        line=line,
        section_hint="debit",
    )

    assert should_continue is True
    assert parsed_row is not None
    assert parsed_row.transaction.amount == -10.0
    assert next_parts == []


def test_accumulate_tabular_row_increments_candidates_without_transaction() -> None:
    transactions: list[pdf_parser_module._ParsedTransaction] = []

    next_candidates = pdf_parser_module._accumulate_tabular_row(
        transactions=transactions,
        parsed_row=None,
        is_candidate=True,
        candidates=2,
    )

    assert next_candidates == 3
    assert transactions == []


def test_accumulate_tabular_row_adds_transaction_when_present() -> None:
    transactions: list[pdf_parser_module._ParsedTransaction] = []
    parsed_row = pdf_parser_module._ParsedTransaction(
        transaction=pdf_parser_module.NormalizedTransaction(
            date="2026-04-10",
            description="Pagamento",
            amount=-10.0,
            type="outflow",
        ),
        source_page=1,
        source_line=1,
    )

    next_candidates = pdf_parser_module._accumulate_tabular_row(
        transactions=transactions,
        parsed_row=parsed_row,
        is_candidate=True,
        candidates=0,
    )

    assert next_candidates == 1
    assert len(transactions) == 1
    assert transactions[0].transaction.description == "Pagamento"


def test_accumulate_tabular_row_reconciles_amount_when_value_matches_balance_by_ocr_noise() -> None:
    transactions: list[pdf_parser_module._ParsedTransaction] = [
        pdf_parser_module._ParsedTransaction(
            transaction=pdf_parser_module.NormalizedTransaction(
                date="2024-03-01",
                description="Linha anterior",
                amount=-150.0,
                type="outflow",
            ),
            source_page=1,
            source_line=1,
            running_balance=-598.66,
        )
    ]
    parsed_row = pdf_parser_module._ParsedTransaction(
        transaction=pdf_parser_module.NormalizedTransaction(
            date="2024-03-01",
            description="ENCARGOS LIMITE DE CRED 310301",
            amount=-600.91,
            type="outflow",
        ),
        source_page=1,
        source_line=2,
        running_balance=-600.91,
    )

    next_candidates = pdf_parser_module._accumulate_tabular_row(
        transactions=transactions,
        parsed_row=parsed_row,
        is_candidate=True,
        candidates=1,
    )

    assert next_candidates == 2
    assert len(transactions) == 2
    assert transactions[1].transaction.amount == -2.25
    assert transactions[1].transaction.type == "outflow"


def test_accumulate_tabular_row_reconciles_single_token_balance_noise_when_sign_is_implicit() -> None:
    transactions: list[pdf_parser_module._ParsedTransaction] = [
        pdf_parser_module._ParsedTransaction(
            transaction=pdf_parser_module.NormalizedTransaction(
                date="2024-03-02",
                description="Linha anterior",
                amount=150.15,
                type="inflow",
            ),
            source_page=1,
            source_line=1,
            running_balance=4712.66,
        )
    ]
    parsed_row = pdf_parser_module._ParsedTransaction(
        transaction=pdf_parser_module.NormalizedTransaction(
            date="2024-03-02",
            description="TARIFA BANCARIA 020324384 FAZ 22",
            amount=-4670.44,
            type="outflow",
        ),
        source_page=1,
        source_line=2,
        running_balance=None,
        has_explicit_amount_sign=False,
    )

    next_candidates = pdf_parser_module._accumulate_tabular_row(
        transactions=transactions,
        parsed_row=parsed_row,
        is_candidate=True,
        candidates=1,
    )

    assert next_candidates == 2
    assert len(transactions) == 2
    assert transactions[1].transaction.amount == -42.22
    assert transactions[1].running_balance == 4670.44


def test_accumulate_tabular_row_keeps_single_token_amount_when_sign_is_explicit() -> None:
    transactions: list[pdf_parser_module._ParsedTransaction] = [
        pdf_parser_module._ParsedTransaction(
            transaction=pdf_parser_module.NormalizedTransaction(
                date="2024-03-02",
                description="Linha anterior",
                amount=150.15,
                type="inflow",
            ),
            source_page=1,
            source_line=1,
            running_balance=4712.66,
        )
    ]
    parsed_row = pdf_parser_module._ParsedTransaction(
        transaction=pdf_parser_module.NormalizedTransaction(
            date="2024-03-02",
            description="DEBITO EXPLICITO",
            amount=-4670.44,
            type="outflow",
        ),
        source_page=1,
        source_line=2,
        running_balance=None,
        has_explicit_amount_sign=True,
    )

    _ = pdf_parser_module._accumulate_tabular_row(
        transactions=transactions,
        parsed_row=parsed_row,
        is_candidate=True,
        candidates=1,
    )

    assert len(transactions) == 2
    assert transactions[1].transaction.amount == -4670.44
    assert transactions[1].running_balance is None


def test_parse_pdf_transactions_prefers_caixa_landscape_datetime_detail_profile(monkeypatch) -> None:
    native_text = """
    Cliente:
    Conta:
    Data: 08/09/2025
    Saldo proprio
    R$ 874,95 C
    Saldo bloqueado
    R$ 0,00 C
    Limite contratado
    R$ 1.000,00 C
    Saldo
    R$ 3.978,14 C
    4 de agosto de 2025, segunda-feira
    Data/Hora
    Nr. Doc.
    Descricao/Detalhamento
    Valor (R$)
    Saldo(R$)
    02/08/2025
    03:32:14
    310725
    COB INTERN
    582,18 C
    522,18 C
    02/08/2025
    03:32:14
    310725
    COB C BANC
    1.888,89 C
    2.411,07 C
    02/08/2025
    03:32:14
    310725
    COB LOT DH
    1.084,85 C
    3.495,92 C
    02/08/2025
    03:32:14
    310725
    COB COMPE
    10.197,30 C
    13.693,22 C
    02/08/2025
    03:32:14
    10825
    COB INTERN
    5,16 D
    13.688,06 C
    02/08/2025
    03:32:14
    10825
    COB C BANC
    10,32 D
    13.677,74 C
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "caixa_extrato_paisagem_data_hora_detalhamento_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 6
    assert result.transactions[0].date == "2025-08-02"
    assert result.transactions[0].description == "03:32:14 310725 COB INTERN"
    assert result.transactions[0].amount == 582.18
    assert result.transactions[-1].description == "03:32:14 10825 COB C BANC"
    assert result.transactions[-1].amount == -10.32


def test_parse_pdf_transactions_prefers_caixa_gerenciador_period_effective_date_profile(monkeypatch) -> None:
    native_text = """
    GERENCIADOR
    C
    A
    I
    X
    A
    .
    CNPJ:
    Agencia:
    Conta:
    02/12/2025 11:22:20
    Saldo anterior ao periodo solicitado
    R$ 44.826,29 C
    Extrato no periodo de 01/11/2025 a 30/11/2025
    Data
    Data Efetiva
    Documento
    Historico
    Valor
    Saldo
    03/11/2025
    01/11 22:19
    012219
    CRED PIX QR
    R$ 15,20
    R$ 44.841,49 C
    03/11/2025
    03/11 07:34
    006703
    AZCX MC CC
    R$ 787,88
    R$ 45.629,37 C
    03/11/2025
    03/11 07:34
    006704
    AZCX VS CC
    R$ 346,76
    R$ 45.976,13 C
    03/11/2025
    03/11 07:34
    006726
    AZCX VS CD
    R$ 114,38
    R$ 46.090,51 C
    03/11/2025
    03/11 11:57
    031157
    DEB PIX CHAVE
    - R$ 140,00
    R$ 45.950,51 C
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "caixa_gerenciador_extrato_periodo_data_efetiva_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert len(result.transactions) == 5
    assert all(transaction.description != "SALDO ANTERIOR" for transaction in result.transactions)
    assert result.transactions[0].description == "01/11 22:19 012219 CRED PIX QR"
    assert result.transactions[0].amount == 15.2
    assert result.transactions[-1].description == "03/11 11:57 031157 DEB PIX CHAVE"
    assert result.transactions[-1].amount == -140.0
    assert result.canonical_transactions[0].running_balance == 44841.49


def test_parse_pdf_transactions_prefers_caixa_sihex_tabular_profile(monkeypatch) -> None:
    native_text = """
    CAI
    X
    A
    SIHEX
    Sistema de Historico de Extratos
    Data:
    Pagina:
    Cliente:
    Agencia:
    Periodo de solicitacao do Extrato:
    CPF/CNPJ:
    Operacao:
    Conta:
    Data Mov.
    Nr. Doc.
    Historico
    Valor
    Saldo
    SALDO ANTERIOR
    140,63 C
    03/01/2022
    093303
    CR VD CART
    4,93 C
    03/01/2022
    093304
    CR VD CART
    304,30 C
    03/01/2022
    104285
    CR VD CART
    187,05 C
    03/01/2022
    324288
    CR VD CART
    539,90 C
    03/01/2022
    445723
    CR VD CART
    203,60 C
    03/01/2022
    445724
    CR VD CART
    147,24 C
    03/01/2022
    671303
    CR VD CART
    492,63 C
    2.160,91 C
    Data Mov.
    Nr. Doc.
    Historico
    Valor
    Saldo
    21/01/2022
    141740
    CR VD CART
    300,00 C
    21/01/2022
    141741
    CR VD CART
    284,66 C
    21/01/2022
    365495
    CR VD CART
    221,74 C
    21/01/2022
    211209
    SAQUE ATM
    801,81 D
    2.165,50 C
    24/01/2022
    175264
    CR VD CART
    486,33 C
    24/01/2022
    175265
    CR VD CART
    12,05 C
    24/01/2022
    175266
    CR VD CART
    397,36 C
    24/01/2022
    875653
    CR VD CART
    14,75 C
    3.075,99 C
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "caixa_sihex_historico_extratos_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert len(result.transactions) == 15
    assert all(transaction.description != "SALDO ANTERIOR" for transaction in result.transactions)
    assert result.transactions[0].date == "2022-01-03"
    assert result.transactions[0].description == "093303 CR VD CART"
    assert result.transactions[0].amount == 4.93
    assert result.transactions[6].description == "671303 CR VD CART"
    assert result.transactions[10].description == "211209 SAQUE ATM"
    assert result.transactions[10].amount == -801.81
    assert result.transactions[-1].description == "875653 CR VD CART"
    assert result.canonical_transactions[6].running_balance == 2160.91
    assert result.canonical_transactions[-1].running_balance == 3075.99


def test_parse_pdf_transactions_prefers_caixa_historico_conta_tabular_profile(monkeypatch) -> None:
    native_text = """
    CAI
    X
    A
    Extrato Historico da Conta
    .
    Periodo
    Unidade
    Nome da Unidade
    Conta
    Nome do produto
    CPF/CNPJ do Titular
    Titular
    Data Mov.
    Data e Hora
    Nr.Doc.
    Historico
    Taxa (%)
    Valor
    Saldo
    SALDO ANTERIOR
    344.503,88 C
    24/01/2023
    24/01 05:36
    000000000
    CRED CM SALDO PROPRIO MP
    0,177300
    1,17 C
    344.505,05 C
    24/01/2023
    24/01 05:36
    000000000
    CRED JUROS SALD PROPRIO M
    0,500000
    3,30 C
    344.508,35 C
    25/01/2023
    25/01 05:02
    000000000
    CRED CM SALDO PROPRIO MP
    0,214500
    23,45 C
    344.531,80 C
    25/01/2023
    25/01 05:02
    000000000
    CRED JUROS SALD PROPRIO M
    0,500000
    54,77 C
    344.586,57 C
    25/01/2023
    25/01 09:41
    000250941
    CRED PAG INST PIX
    150,00 C
    344.736,57 C
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "caixa_extrato_historico_conta_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert len(result.transactions) == 5
    assert all(transaction.description != "SALDO ANTERIOR" for transaction in result.transactions)
    assert result.transactions[0].date == "2023-01-24"
    assert result.transactions[0].description == "24/01 05:36 000000000 CRED CM SALDO PROPRIO MP 0,177300"
    assert result.transactions[0].amount == 1.17
    assert result.transactions[-1].description == "25/01 09:41 000250941 CRED PAG INST PIX"
    assert result.transactions[-1].amount == 150.0
    assert result.canonical_transactions[0].running_balance == 344505.05
    assert result.canonical_transactions[-1].running_balance == 344736.57


def test_parse_pdf_transactions_prefers_sicoob_creditran_extrato_detalhado_conta_profile(monkeypatch) -> None:
    native_text = """
    SICOOB
    Creditran
    Conta Corrente
    06/02/2025 15:46:18
    Banco:
    - Agencia:
    - Conta Corrente:
    EXTRATO DETALHADO CONTA
    PERIODO DE 01/01/2025 A 31/01/2025
    Ultimos Lancamentos Saldo anterior:
    -3.317,19
    Data
    Historico
    Documento
    Valor
    Saldo
    31/12/2024
    DEB.SEGURO EMPRESTIMO
    0009731610
    -45,41
    -3.362,60
    31/12/2024
    JUROS ADIANT.DEPOSITANTE
    AD/31-12
    -3,75
    -3.366,35
    31/12/2024
    JUROS CONTA GARANTIDA
    LC-202411
    -80,76
    -3.447,11
    31/12/2024
    MANUTENCAO SOFTWARE
    11/2024
    -179,20
    -3.626,31
    31/12/2024
    OUTROS CREDITOS
    11/2024
    224,00
    -3.402,31
    02/01/2025
    DEB.SEGURO EMPRESTIMO
    0009974607
    -47,93
    -3.450,24
    03/01/2025
    TRANSFERENCIA RECEBIDA
    PIX030125
    650,00
    -2.800,24
    04/01/2025
    PAGAMENTO BOLETO
    BLT-0104
    -125,50
    -2.925,74
    06/01/2025
    TARIFA CESTA EMPRESARIAL
    CESTA01
    -32,10
    -2.957,84
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "sicoob_creditran_extrato_detalhado_conta_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert len(result.transactions) == 9
    assert result.transactions[0].date == "2024-12-31"
    assert result.transactions[0].description == "DEB.SEGURO EMPRESTIMO 0009731610"
    assert result.transactions[0].amount == -45.41
    assert result.transactions[4].description == "OUTROS CREDITOS"
    assert result.transactions[4].amount == 224.0
    assert result.transactions[-1].description == "TARIFA CESTA EMPRESARIAL CESTA01"
    assert result.transactions[-1].amount == -32.1


def test_parse_pdf_transactions_prefers_sicoob_poupanca_cooperada_tabular_profile(monkeypatch) -> None:
    native_text = """
    SICOOB - Sistema de Cooperativas de Credito do Brasil
    Plataforma de Servicos Financeiros do Sicoob - SISBR
    Extrato Poupanca Cooperada
    Agencia:
    Conta:
    Data
    Documento
    Historico
    Debito
    Credito
    Saldo
    01/10/2022
    SALDO ANTERIOR
    338,38+
    11/10/2022
    CORRECAO MONETARIA
    - SELIC
    0,24+
    338,62+
    11/10/2022
    JUROS - SELIC
    0,66+
    339,28+
    11/10/2022
    I.R.R.F APL. FIN - SELIC
    0,20-
    339,08+
    25/10/2022
    CORRECAO MONETARIA
    - SELIC
    0,31+
    339,39+
    25/10/2022
    JUROS - SELIC
    1,03+
    340,42+
    25/10/2022
    I.R.R.F APL. FIN - SELIC
    0,30-
    340,12+
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "sicoob_extrato_poupanca_cooperada_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert len(result.transactions) == 6
    assert all(transaction.description != "SALDO ANTERIOR" for transaction in result.transactions)
    assert result.transactions[0].date == "2022-10-11"
    assert result.transactions[0].description == "CORRECAO MONETARIA - SELIC"
    assert result.transactions[0].amount == 0.24
    assert result.transactions[2].description == "I.R.R.F APL. FIN - SELIC"
    assert result.transactions[2].amount == -0.2
    assert result.transactions[-1].description == "I.R.R.F APL. FIN - SELIC"
    assert result.transactions[-1].amount == -0.3
    assert result.canonical_transactions[0].running_balance == 338.62
    assert result.canonical_transactions[-1].running_balance == 340.12


def test_parse_pdf_transactions_prefers_sicoob_apropriacao_diaria_tabular_profile(monkeypatch) -> None:
    native_text = """
    - SICOOB -
    Sistema de Cooperativas de Credito do Brasil
    Plataforma de Servicos Financeiros do Sicoob - SISBR
    Extrato de Apropriacao Diaria
    01/08/2023
    10:38:02
    MODALIDADE:
    RDC - LONGO POS CDI
    CONTA:
    NOME:
    N APPLICACAO:
    DATA FIM DA CARENCIA/VENC.:
    DATA DA APLICACAO:
    Data
    Historico
    Valor
    30/06/2023
    SALDO ANTERIOR
    R$ 7,49C
    04/07/2023
    APROPRIACAO DE CM
    R$ 0,01C
    06/07/2023
    APROPRIACAO DE CM
    R$ 0,01C
    11/07/2023
    APROPRIACAO DE CM
    R$ 0,01C
    13/07/2023
    APROPRIACAO DE CM
    R$ 0,01C
    18/07/2023
    APROPRIACAO DE CM
    R$ 0,01C
    21/07/2023
    APROPRIACAO DE CM
    R$ 0,01C
    25/07/2023
    APROPRIACAO DE CM
    R$ 0,01C
    27/07/2023
    RESGATE DE APLICACAO FINANCEIRA
    R$ 5,02D
    27/07/2023
    RETENCAO DE IRRF
    R$ 0,15D
    28/07/2023
    APROPRIACAO DE CM
    R$ 0,01C
    RESUMO
    SALDO BRUTO EM 28/07/2023 : R$ 2,40
    SALDO DISPONIVEL EM 28/07/2023 : R$ 2,33
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [native_text])
    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", lambda raw_bytes: [native_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert result.layout.layout_name == "sicoob_extrato_apropriacao_diaria_v1"
    assert result.layout.used_fallback is False
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert len(result.transactions) == 10
    assert all(transaction.description != "SALDO ANTERIOR" for transaction in result.transactions)
    assert result.transactions[0].date == "2023-07-04"
    assert result.transactions[0].description == "APROPRIACAO DE CM"
    assert result.transactions[0].amount == 0.01
    assert result.transactions[7].description == "RESGATE DE APLICACAO FINANCEIRA"
    assert result.transactions[7].amount == -5.02
    assert result.transactions[-1].description == "APROPRIACAO DE CM"
    assert result.transactions[-1].amount == 0.01
