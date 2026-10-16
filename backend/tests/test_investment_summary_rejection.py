from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.document_routing_policy import (
    DocumentRoutingAction,
    enforce_document_routing,
)
from app.application.document_type_classifier import (
    BANK_STATEMENT,
    DocumentTypeClassification,
    classify_document_type,
)
from app.application.errors import UnsupportedDocumentContentError
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.investment_summaries import (
    CDB_POSITION_SUMMARY,
    INVESTMENT_SUMMARIES,
)


@pytest.mark.parametrize("text", INVESTMENT_SUMMARIES, ids=["cdb-position", "principal-gross-net"])
def test_classifies_investment_summary_before_generic_statement_layout(text: str) -> None:
    result = classify_document_type(
        "extrato.pdf",
        b"%PDF synthetic",
        extracted_text=text,
        layout_inference_name="generic_statement_ptbr",
        layout_inference_confidence=0.99,
    )

    assert result.document_type == "investment_summary"
    assert result.confidence >= 0.9
    assert result.evidence == ["investment_summary_structure"]


@pytest.mark.parametrize("text", INVESTMENT_SUMMARIES, ids=["cdb-position", "principal-gross-net"])
def test_native_investment_summary_is_rejected_before_transaction_parsing(text: str) -> None:
    raw_bytes = generate_native_text_pdf((tuple(text.strip().splitlines()),))

    def unexpected_parser(*args, **kwargs):
        raise AssertionError("An investment summary must not reach the transaction parser")

    with pytest.raises(UnsupportedDocumentContentError) as exc_info:
        build_default_conversion_pipeline().run(
            filename="extrato.pdf",
            raw_bytes=raw_bytes,
            analysis_id="an_summary_rejected",
            pdf_parser=unexpected_parser,
        )

    assert exc_info.value.document_type == "investment_summary"
    assert exc_info.value._document_type_classification.document_type == "investment_summary"
    assert "resumo ou posição de investimentos" in str(exc_info.value)


def test_investment_summary_routing_rejects_only_high_confidence() -> None:
    classification = DocumentTypeClassification("investment_summary", 0.99, ["investment_summary_structure"])

    with pytest.raises(UnsupportedDocumentContentError) as exc_info:
        enforce_document_routing(classification)

    assert exc_info.value.document_type == "investment_summary"
    assert exc_info.value._document_type_classification == classification
    assert enforce_document_routing(
        DocumentTypeClassification("investment_summary", 0.5)
    ).action == DocumentRoutingAction.CONTINUE


@pytest.mark.parametrize("text", INVESTMENT_SUMMARIES, ids=["cdb-position", "principal-gross-net"])
def test_pdf_parser_rejects_summary_without_ocr_retry(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_bytes = generate_native_text_pdf((tuple(text.strip().splitlines()),))
    monkeypatch.setattr(pdf_parser_module, "is_textract_enabled", lambda: False)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)

    def unexpected_ocr(*args, **kwargs):
        raise AssertionError("Readable investment summaries must not retry OCR")

    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", unexpected_ocr)

    with pytest.raises(UnsupportedDocumentContentError) as exc_info:
        pdf_parser_module.parse_pdf_transactions(raw_bytes)

    assert exc_info.value.document_type == "investment_summary"
    assert exc_info.value._parse_observability["ocr_retry_skip_reason"] == "unsupported_document_type"


@pytest.mark.parametrize("text", INVESTMENT_SUMMARIES, ids=["cdb-position", "principal-gross-net"])
def test_textract_text_summary_does_not_fall_back_to_transaction_adapter(
    text: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    extraction = SimpleNamespace(
        pages=[SimpleNamespace(lines=[
            SimpleNamespace(text=line, line_index=index)
            for index, line in enumerate(text.strip().splitlines())
        ])]
    )

    def unexpected_adapter(*args, **kwargs):
        raise AssertionError("Rejected OCR content must not become transactions through another adapter")

    monkeypatch.setattr(pdf_parser_module, "adapt_textract_extraction_to_transactions", unexpected_adapter)

    with pytest.raises(UnsupportedDocumentContentError) as exc_info:
        pdf_parser_module._parse_scanned_pdf_with_textract_text_pages(extraction)

    assert exc_info.value.document_type == "investment_summary"


def test_summary_rejected_by_textract_does_not_retry_local_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw: [])
    monkeypatch.setattr(pdf_parser_module, "is_textract_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)

    def rejected_textract(raw_bytes):
        raise UnsupportedDocumentContentError(document_type="investment_summary", message="Resumo de investimentos")

    def unexpected_ocr(*args, **kwargs):
        raise AssertionError("An unsupported document must not be retried with local OCR")

    monkeypatch.setattr(pdf_parser_module, "_parse_scanned_pdf_with_textract_gateway", rejected_textract)
    monkeypatch.setattr(pdf_parser_module, "_parse_scanned_pdf_with_local_ocr_adapter", unexpected_ocr)

    with pytest.raises(UnsupportedDocumentContentError) as exc_info:
        pdf_parser_module.parse_pdf_transactions(b"%PDF synthetic")

    assert exc_info.value.document_type == "investment_summary"


@pytest.mark.parametrize("summary", INVESTMENT_SUMMARIES, ids=["cdb-position", "principal-gross-net"])
@pytest.mark.parametrize("description", ["PIX RECEBIDO", "BOLETO PAGO"])
def test_statement_with_investment_summary_appendix_is_not_rejected(summary: str, description: str) -> None:
    statement = f"""
    EXTRATO DE CONTA CORRENTE
    DATA HISTÓRICO VALOR SALDO
    01/09/2026 {description} -100,00 900,00
    """

    result = classify_document_type("extrato.pdf", b"%PDF synthetic", extracted_text=statement + summary)

    assert result.document_type == BANK_STATEMENT
    assert enforce_document_routing(result).action == DocumentRoutingAction.CONTINUE


@pytest.mark.parametrize("text", [
    "EXTRATO CDB-DI\nDATA HISTÓRICO VALOR SALDO",
    "Resumo\nPrincipal Bruto Líquido\nValor Saldo",
    "Extrato de conta corrente\n01/09/2026 PAGAMENTO RESUMO DE INVESTIMENTOS -10,00 90,00",
])
def test_investment_words_alone_do_not_trigger_summary_rejection(text: str) -> None:
    classification = classify_document_type("extrato.pdf", b"%PDF synthetic", extracted_text=text)

    assert classification.document_type != "investment_summary"


@pytest.mark.parametrize("text", [
    CDB_POSITION_SUMMARY.replace("31/08/2026 SALDO ANTERIOR", "31/08/2026 APLICAÇÃO"),
    CDB_POSITION_SUMMARY.replace("31/08/2026 SALDO ANTERIOR", "31/08/2026\nRESGATE"),
])
def test_cdb_with_dated_cash_movements_is_not_classified_as_summary(text: str) -> None:
    classification = classify_document_type("cdb.pdf", b"%PDF synthetic", extracted_text=text)

    assert classification.document_type != "investment_summary"


@pytest.mark.parametrize("text,layout,expected_amounts", [
    (
        """
        Banco do Brasil
        Extratos - CDB / RDB e BB Reaplic
        Período 01/09/2026 a 30/09/2026
        Data Dt.proc Histórico Nr.depósito Valor
        01/09 Aplicação - 9000000000001
        valor capital 1.000,00
        30/09 Resgate - 9000000000001
        valor capital 100,00
        valor juros no mês 10,00
        valor IR 2,00-
        valor líquido 108,00
        RESUMO DOS DEPOSITOS EM SER
        Numero Dt.aplic Capital Inicial Saldo de Capital Taxa Dt.vcto
        9000000000001 01/09/2026 1.000,00 900,00 100,00 30/09/2027
        """,
        "banco_do_brasil_cdb_rdb_bb_reaplic_v1",
        [-1000.0, 108.0],
    ),
    (
        """
        Histórico de movimentação
        Data Histórico Capital Rendimento* IR IOF Valor Líquido
        31/08/2026 Saldo Anterior 1.000,00 25,00 0,00 0,00 1.025,00
        01/09/2026 Aplicação 100,00 0,00 0,00 0,00 100,00
        02/09/2026 Resgate 80,00 5,00 1,00 0,50 83,50
        BB Rende Fácil
        Cliente: CLIENTE EXEMPLO
        Agência: 0001 Conta: 12345-6
        """,
        "banco_do_brasil_rende_facil_historico_movimentacao_v1",
        [-100.0, 83.5],
    ),
])
def test_pipeline_preserves_supported_investment_movement_layouts(
    text: str, layout: str, expected_amounts: list[float]
) -> None:
    raw_bytes = generate_native_text_pdf((tuple(text.strip().splitlines()),))

    result = build_default_conversion_pipeline().run(
        filename="investimento.pdf", raw_bytes=raw_bytes, analysis_id="an_supported_investment"
    )

    assert result.analysis_data.layout_inference_name == layout
    assert [row.amount for row in result.analysis_data.report_transactions] == expected_amounts
