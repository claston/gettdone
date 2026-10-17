import pytest

from app.application.document_type_classifier import classify_document_type
from tests.fixtures.accounts_payable import ACCOUNTS_PAYABLE_REPORT


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


@pytest.mark.parametrize("text", [
    "Vencimento Pagamento Fornecedor Valor total Em aberto Situação\n01/09/2026 100,00",
    "EXTRATO DE CONTA CORRENTE\nDATA HISTÓRICO VALOR SALDO\n"
    "01/09/2026 PAGAMENTO RELATÓRIO DE CONTAS A PAGAR -10,00 90,00",
    "EXTRATO DE CONTA CORRENTE\nDATA HISTÓRICO VALOR SALDO\n"
    "01/09/2026 PIX RECEBIDO 100,00 100,00\n" + ACCOUNTS_PAYABLE_REPORT,
])
def test_report_words_or_appendix_do_not_reclassify_statement(text):
    assert classify_document_type("statement.pdf", b"", extracted_text=text).document_type != "accounts_payable_report"


def test_report_title_is_recognized_without_guessing_transactions_from_missing_columns():
    classification = classify_document_type("report.pdf", b"", extracted_text="Relatório de Contas a Pagar\nValor 100,00")
    assert classification.document_type == "accounts_payable_report"
    assert classification.confidence < 0.9
