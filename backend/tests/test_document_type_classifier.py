from __future__ import annotations

import pytest

from app.application.conversion_pipeline import ConversionPipeline
from app.application.document_type_classifier import (
    BANK_RECEIPT,
    BANK_STATEMENT,
    BILLING_REPORT,
    CREDIT_CARD_STATEMENT,
    FINANCIAL_SPREADSHEET,
    FISCAL_INVOICE,
    UNKNOWN_DOCUMENT,
    DocumentTypeClassification,
    classify_document_type,
)
from app.application.errors import InvalidFileContentError, UnsupportedDocumentContentError
from app.application.models import NormalizedTransaction
from app.application.parsers.service import ParsedDocument


def test_classifies_nfe_from_multiple_strong_signals_without_exposing_document_text() -> None:
    text = """
    DANFE
    Documento Auxiliar da Nota Fiscal Eletrônica
    CHAVE DE ACESSO
    3526 1000 0000 0000 0000 5500 1000 0000 0010 0000 0001
    PROTOCOLO DE AUTORIZAÇÃO DE USO 135260000000000
    EMITENTE DESTINATÁRIO
    DADOS DOS PRODUTOS / SERVIÇOS
    """

    result = classify_document_type("documento.pdf", b"%PDF", extracted_text=text)

    assert result.document_type == FISCAL_INVOICE
    assert result.confidence >= 0.9
    assert "nfe_danfe" in result.evidence
    assert "nfe_access_key" in result.evidence
    assert all("3526" not in item for item in result.evidence)


def test_does_not_classify_bank_statement_as_invoice_from_transaction_description() -> None:
    text = """
    EXTRATO DE CONTA CORRENTE
    AGÊNCIA 0001 CONTA 12345-6
    DATA HISTÓRICO DOCUMENTO VALOR SALDO
    01/10/2026 PAGAMENTO NOTA FISCAL FORNECEDOR 123 -150,00 850,00
    02/10/2026 PIX RECEBIDO CLIENTE 500,00 1.350,00
    """

    result = classify_document_type("extrato.pdf", b"%PDF", extracted_text=text)

    assert result.document_type == BANK_STATEMENT
    assert result.confidence >= 0.85


@pytest.mark.parametrize(
    "transaction_text",
    [
        "01/10/2026 PAGAMENTO FATURA CARTÃO VENCIMENTO 10/10/2026 -150,00 850,00",
        "01/10/2026 PIX VALOR DATA DA TRANSAÇÃO -150,00 850,00",
    ],
)
def test_requires_document_level_card_or_receipt_title(transaction_text: str) -> None:
    text = f"""
    EXTRATO DE CONTA CORRENTE
    DATA HISTÓRICO DOCUMENTO VALOR SALDO
    {transaction_text}
    02/10/2026 PIX RECEBIDO CLIENTE 500,00 1.350,00
    """

    result = classify_document_type("extrato.pdf", b"%PDF", extracted_text=text)

    assert result.document_type == BANK_STATEMENT


@pytest.mark.parametrize(
    ("filename", "text", "expected_type"),
    [
        (
            "relatorio.pdf",
            "RELATÓRIO DE FATURAMENTO JANEIRO FEVEREIRO MARÇO ABRIL MAIO JUNHO PERÍODO TOTAIS CNPJ IMPOSTOS",
            BILLING_REPORT,
        ),
        (
            "comprovante.pdf",
            "COMPROVANTE DE TRANSFERÊNCIA PIX VALOR DATA DA TRANSAÇÃO ID DA TRANSAÇÃO AUTENTICAÇÃO",
            BANK_RECEIPT,
        ),
        (
            "fatura.pdf",
            "FATURA DO CARTÃO DE CRÉDITO VENCIMENTO PAGAMENTO MÍNIMO LIMITE DISPONÍVEL",
            CREDIT_CARD_STATEMENT,
        ),
    ],
)
def test_classifies_other_known_pdf_document_types(filename: str, text: str, expected_type: str) -> None:
    result = classify_document_type(filename, b"%PDF", extracted_text=text)

    assert result.document_type == expected_type
    assert result.confidence >= 0.8


def test_classifies_ofx_as_bank_statement() -> None:
    result = classify_document_type(
        "movimentos.ofx",
        b"<OFX><BANKMSGSRSV1><STMTRS><BANKTRANLIST>",
    )

    assert result.document_type == BANK_STATEMENT
    assert result.confidence == 1.0


def test_classifies_generic_csv_as_financial_spreadsheet() -> None:
    result = classify_document_type(
        "controle.csv",
        b"data,descricao,valor\n2026-10-01,Receita,100.00\n",
    )

    assert result.document_type == FINANCIAL_SPREADSHEET
    assert result.confidence >= 0.8


def test_returns_unknown_when_there_is_not_enough_evidence() -> None:
    result = classify_document_type("arquivo.pdf", b"%PDF", extracted_text="Documento sem marcadores suficientes")

    assert result.document_type == UNKNOWN_DOCUMENT
    assert result.confidence == 0.0
    assert result.evidence == []


def test_conversion_pipeline_classifies_before_parsing_and_exposes_result() -> None:
    events: list[str] = []

    class RecordingParser:
        def parse(self, document, **kwargs) -> ParsedDocument:
            events.append("parse")
            return ParsedDocument(
                file_type=document.file_type,
                transactions=[
                    NormalizedTransaction(
                        date="2026-10-01",
                        description="PIX RECEBIDO",
                        amount=100.0,
                        type="credit",
                    )
                ],
                extracted_text="EXTRATO DATA HISTÓRICO VALOR SALDO",
                layout_inference_name="test_statement_v1",
                layout_inference_confidence=0.99,
            )

    def recording_classifier(**kwargs) -> DocumentTypeClassification:
        events.append("classify")
        return DocumentTypeClassification(
            document_type=BANK_STATEMENT,
            confidence=0.99,
            evidence=["test_rule"],
        )

    result = ConversionPipeline(
        parser=RecordingParser(),
        document_type_classifier=recording_classifier,
    ).run(
        filename="extrato.csv",
        raw_bytes=b"data,descricao,valor\n2026-10-01,PIX,100.00\n",
        analysis_id="an_document_type",
    )

    assert events == ["classify", "parse", "classify"]
    assert result.document_type_classification.document_type == BANK_STATEMENT
    assert result.analysis_data.document_type == BANK_STATEMENT
    assert result.analysis_data.document_type_confidence == 0.99
    assert result.analysis_data.document_type_evidence == ["test_rule"]


def test_conversion_pipeline_attaches_preclassification_to_parser_failure() -> None:
    classification = DocumentTypeClassification(
        document_type=BANK_STATEMENT,
        confidence=0.97,
        evidence=["statement_title", "statement_balance"],
    )

    class FailingParser:
        def parse(self, document, **kwargs) -> ParsedDocument:
            raise InvalidFileContentError("no recognizable transaction row pattern")

    pipeline = ConversionPipeline(
        parser=FailingParser(),
        document_type_classifier=lambda **kwargs: classification,
    )

    with pytest.raises(InvalidFileContentError) as exc_info:
        pipeline.run(
            filename="nfe.pdf",
            raw_bytes=b"%PDF synthetic",
            analysis_id="an_document_type_failure",
        )

    assert exc_info.value._document_type_classification == classification


def test_conversion_pipeline_rejects_high_confidence_nfe_before_parser() -> None:
    classification = DocumentTypeClassification(
        document_type=FISCAL_INVOICE,
        confidence=0.93,
        evidence=["nfe_danfe", "nfe_access_key", "nfe_parties"],
    )
    parser_called = False

    class RecordingParser:
        def parse(self, document, **kwargs) -> ParsedDocument:
            nonlocal parser_called
            parser_called = True
            raise AssertionError("the parser must not run for a high-confidence NF-e")

    pipeline = ConversionPipeline(
        parser=RecordingParser(),
        document_type_classifier=lambda **kwargs: classification,
    )

    with pytest.raises(UnsupportedDocumentContentError) as exc_info:
        pipeline.run(
            filename="nfe.pdf",
            raw_bytes=b"%PDF synthetic",
            analysis_id="an_nfe_rejected",
        )

    assert exc_info.value._document_type_classification == classification
    assert parser_called is False


def test_conversion_pipeline_rejects_nfe_identified_from_extracted_text() -> None:
    initial_classification = DocumentTypeClassification(
        document_type=UNKNOWN_DOCUMENT,
        confidence=0.0,
        evidence=[],
    )
    refined_classification = DocumentTypeClassification(
        document_type=FISCAL_INVOICE,
        confidence=0.93,
        evidence=["nfe_danfe", "nfe_access_key", "nfe_parties"],
    )
    classifications = iter([initial_classification, refined_classification])

    class InvoiceParser:
        def parse(self, document, **kwargs) -> ParsedDocument:
            return ParsedDocument(
                file_type=document.file_type,
                transactions=[],
                extracted_text="DANFE CHAVE DE ACESSO EMITENTE DESTINATÁRIO",
            )

    pipeline = ConversionPipeline(
        parser=InvoiceParser(),
        document_type_classifier=lambda **kwargs: next(classifications),
    )

    with pytest.raises(UnsupportedDocumentContentError) as exc_info:
        pipeline.run(
            filename="nfe-digitalizada.pdf",
            raw_bytes=b"%PDF synthetic",
            analysis_id="an_nfe_refined",
        )

    assert exc_info.value._document_type_classification == refined_classification
