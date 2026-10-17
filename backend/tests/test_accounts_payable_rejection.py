"""Reject payables as account statements using synthetic inputs only."""
import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction
from app.application.document_routing_policy import DocumentRoutingAction, enforce_document_routing
from app.application.document_type_classifier import DocumentTypeClassification, classify_document_type
from app.application.errors import UnsupportedDocumentContentError
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.accounts_payable import ACCOUNTS_PAYABLE_REPORT


def report_pdf(text=ACCOUNTS_PAYABLE_REPORT):
    return generate_native_text_pdf((tuple(text.strip().splitlines()),))


@pytest.mark.parametrize("text", [
    ACCOUNTS_PAYABLE_REPORT,
    ACCOUNTS_PAYABLE_REPORT.upper(),
    ACCOUNTS_PAYABLE_REPORT.replace("Relatório", "Relatorio").replace("Situação", "Situacao"),
    ACCOUNTS_PAYABLE_REPORT.replace("Vencimento Pagamento", "Vencimento\nPagamento"),
], ids=["printed", "uppercase", "without-accents", "wrapped-header"])
def test_report_identity_overrides_generic_statement_layout(text):
    classification = classify_document_type(
        "report.pdf", b"", extracted_text=text,
        layout_inference_name="generic_statement_ptbr", layout_inference_confidence=0.99,
    )
    assert classification.document_type == "accounts_payable_report"
    assert classification.confidence >= 0.9
    assert classification.evidence == ["accounts_payable_report_structure"]


def test_native_report_is_rejected_before_transaction_parser():
    def unexpected_parser(*args, **kwargs):
        pytest.fail("Payables must be identified before attempting financial conversion")

    with pytest.raises(UnsupportedDocumentContentError) as caught:
        build_default_conversion_pipeline().run(
            filename="synthetic-report.pdf", raw_bytes=report_pdf(), analysis_id="synthetic-payables",
            pdf_parser=unexpected_parser,
        )
    assert caught.value.document_type == "accounts_payable_report"
    assert caught.value._document_type_classification.document_type == "accounts_payable_report"
    assert "relatório de contas a pagar" in str(caught.value)
    assert "não um extrato bancário" in str(caught.value)


def test_only_confident_report_classification_rejects():
    with pytest.raises(UnsupportedDocumentContentError):
        enforce_document_routing(DocumentTypeClassification("accounts_payable_report", 0.99))
    assert enforce_document_routing(
        DocumentTypeClassification("accounts_payable_report", 0.5)
    ).action == DocumentRoutingAction.CONTINUE


def test_readable_report_does_not_trigger_ocr(monkeypatch):
    monkeypatch.setattr(pdf_parser, "is_textract_enabled", lambda: False)
    monkeypatch.setattr(pdf_parser, "is_pdf_ocr_enabled", lambda: True)

    def unexpected_ocr(*args, **kwargs):
        pytest.fail("Readable unsupported reports must not spend OCR calls")

    monkeypatch.setattr(pdf_parser, "extract_pdf_page_texts_with_ocr", unexpected_ocr)
    with pytest.raises(UnsupportedDocumentContentError) as caught:
        pdf_parser.parse_pdf_transactions(report_pdf())
    assert caught.value.document_type == "accounts_payable_report"
    assert caught.value._parse_observability["ocr_retry_skip_reason"] == "unsupported_document_type"


def test_ocr_report_does_not_fall_back_to_transaction_adapter(monkeypatch):
    extraction = RawDocumentExtraction(
        provider="textract", document_hash="synthetic", metrics={},
        pages=[ExtractedPage(1, lines=[
            ExtractedLine(str(index), 1, index, text)
            for index, text in enumerate(ACCOUNTS_PAYABLE_REPORT.splitlines())
        ])],
    )

    def unexpected_adapter(*args, **kwargs):
        pytest.fail("Unsupported reports must not become transactions through another adapter")

    monkeypatch.setattr(pdf_parser, "adapt_textract_extraction_to_transactions", unexpected_adapter)
    with pytest.raises(UnsupportedDocumentContentError) as caught:
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)
    assert caught.value.document_type == "accounts_payable_report"


@pytest.mark.parametrize("text", [
    "Relatório de Contas a Pagar\nValor 100,00",
    "Vencimento Pagamento Fornecedor Valor total Em aberto Situação\n01/09/2026 100,00",
    "EXTRATO DE CONTA CORRENTE\nDATA HISTÓRICO VALOR SALDO\n"
    "01/09/2026 PAGAMENTO RELATÓRIO DE CONTAS A PAGAR -10,00 90,00",
    "EXTRATO DE CONTA CORRENTE\nDATA HISTÓRICO VALOR SALDO\n"
    "01/09/2026 PIX RECEBIDO 100,00 100,00\n" + ACCOUNTS_PAYABLE_REPORT,
])
def test_report_words_or_appendix_do_not_reject_statement(text):
    assert classify_document_type("statement.pdf", b"", extracted_text=text).document_type != "accounts_payable_report"


def test_bank_statement_mentioning_payables_still_converts(monkeypatch):
    monkeypatch.setattr(pdf_parser, "is_textract_enabled", lambda: False)
    text = "EXTRATO DE CONTA CORRENTE\n01/09/2026 PAGAMENTO RELATÓRIO DE CONTAS A PAGAR -10,00\n02/09/2026 PIX RECEBIDO 50,00"
    data = build_default_conversion_pipeline().run(
        filename="statement.pdf", raw_bytes=report_pdf(text), analysis_id="synthetic-allowed-statement",
    ).analysis_data
    assert data.transactions_total == 2
    assert data.net_total == 40.0
