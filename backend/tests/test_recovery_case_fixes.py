"""Fictional regression cases for the recovery audit; no customer data."""

import pytest

from app.application import pdf_parser
from app.application.document_routing_policy import enforce_document_routing
from app.application.document_type_classifier import classify_document_type
from app.application.errors import PdfFinancialValidationError, UnsupportedDocumentContentError
from app.application.parsers.pdf.verified_statements import parse_verified_statement


def sicredi_pages():
    return [
        f"""Cooperativa: 0001 Conta: 12345-6
Extrato(Período de 01/{month}/2026 a {end}/{month}/2026)
Data Descrição Documento Valor(R$) Saldo (R$)
SALDO ANTERIOR {opening},00
02/{month}/2026 PIX TESTE PIX_CRED 10,00 {closing},00
SicrediFone 0800 000 0000"""
        for month, end, opening, closing in [("06", "30", 100, 110), ("07", "31", 110, 120), ("08", "31", 120, 130)]
    ]


def test_sicredi_consecutive_months_validate_each_opening_and_all_rows():
    statement = parse_verified_statement(sicredi_pages())
    assert len(statement.rows) == 3
    assert statement.opening == 100 and statement.closing == 130
    assert statement.printed_balance_checks >= 3


@pytest.mark.parametrize("old,new", [("110,00", "111,00"), ("12345-6", "98765-4"), ("01/07", "01/08")])
def test_sicredi_rejects_discontinuous_balances_accounts_or_periods(old, new):
    pages = sicredi_pages()
    pages[1] = pages[1].replace(old, new)
    with pytest.raises(PdfFinancialValidationError):
        parse_verified_statement(pages)


def mercado_pago_page(refund="Reembolso de Pix enviado DESTINO TESTE", amount="20,00", reference="1002"):
    return f"""EXTRATO DE CONTA
Periodo: De 01-07-2026 al 31-07-2026
Saldo inicial: R$ 10,00 Entradas: R$ 70,00
Saidas: R$ -20,00 Saldo final: R$ 60,00
Data Descrição ID da operação Valor Saldo
02-07-2026 Pix recebido COMERCIO TESTE 1001 R$ 50,00 R$ 60,00
03-07-2026 Pix enviado DESTINO TESTE 1002 R$ -20,00 R$ 40,00
04-07-2026 {refund} {reference} R$ {amount} R$ 60,00
Mercado Pago Instituição de Pagamento Ltda.
1/1"""


def test_mercado_pago_refund_shares_operation_id_without_dropping_either_event():
    statement = parse_verified_statement([mercado_pago_page()])
    assert [r.transaction.amount for r in statement.rows] == [50, -20, 20]
    assert [r.external_reference_id for r in statement.rows] == ["1001", "1002", "1002"]


def test_mercado_pago_refund_label_can_be_the_whole_description():
    statement = parse_verified_statement([mercado_pago_page(refund="Reembolso de Pix enviado")])
    assert len(statement.rows) == 3


@pytest.mark.parametrize("refund", ["Pix recebido TESTE", "Reembolso de pagamento TESTE", "Pix enviado TESTE"])
def test_mercado_pago_duplicate_id_requires_explicit_pix_refund(refund):
    with pytest.raises(PdfFinancialValidationError):
        parse_verified_statement([mercado_pago_page(refund=refund)])


@pytest.mark.parametrize("pages", [
    ["Santander EXTRATO CONSOLIDADO INTELIGENTE\nPagina: 1/3"],
    ["Bradesco Extrato Mensal\nPágina:\n3\nde\n8", "Página:\n4\nde\n8"],
])
def test_printed_missing_pages_fail_before_generic_parser(pages):
    with pytest.raises(PdfFinancialValidationError, match="páginas.*completo"):
        pdf_parser._parse_pdf_transactions_from_page_texts(pages)


def test_cnab_return_is_classified_and_explained_instead_of_unknown():
    text = "02RETORNO01COBRANCA" + "0" * 40 + "237BRADESCO" + "0" * 300 + "\n" + "1" + "0" * 399
    classification = classify_document_type("retorno.pdf", b"", extracted_text=text)
    assert classification.document_type == "bank_collection_return"
    assert classification.confidence >= .99
    with pytest.raises(UnsupportedDocumentContentError, match="CNAB.*extrato bancário"):
        enforce_document_routing(classification)


def test_cnab_mention_inside_statement_does_not_change_document_type():
    text = "Extrato de Conta Corrente\nAgência: 0001 Conta: 12345-6\nData Histórico Valor Saldo\n01/07/2026 CNAB RETORNO 10,00 C 100,00 C"
    assert classify_document_type("extrato.pdf", b"", extracted_text=text).document_type != "bank_collection_return"


def test_printed_cnab_header_may_wrap_across_pdf_text_objects():
    text = "02RETORNO01COBRANCA " + "0" * 30 + "\n237BRADESCO\n" + "1" + "0" * 70
    assert classify_document_type("retorno.pdf", b"", extracted_text=text).document_type == "bank_collection_return"
    manual = "02RETORNO01COBRANCA\nExemplo de cabeçalho de retorno"
    assert classify_document_type("manual.pdf", b"", extracted_text=manual).document_type == "unknown"


def test_cnab_classification_is_preserved_in_persisted_metrics():
    from app.application.document_classification_history import prepare_document_classification_record

    record = prepare_document_classification_record(document_type="bank_collection_return", document_type_confidence=.99,
        document_classification_version="2026-10-23.1", document_processing_decision="rejected",
        document_classification_evidence=["cnab_400_collection_return_header"])
    assert record[0] == "bank_collection_return" and record[3] == "rejected"


def test_sicredi_last_period_needs_its_own_opening_control():
    pages = sicredi_pages()
    pages[-1] = pages[-1].split("SALDO ANTERIOR")[0]
    with pytest.raises(PdfFinancialValidationError):
        parse_verified_statement(pages)


def test_sicredi_future_section_does_not_hide_the_next_month():
    pages = [page + "\nLançamentos Futuros\n01/10/2026 PAGAMENTO FUTURO -99,00" for page in sicredi_pages()]
    assert len(parse_verified_statement(pages).rows) == 3


def test_mercado_pago_third_use_of_operation_id_is_not_a_second_refund():
    page = mercado_pago_page().replace("Entradas: R$ 70,00", "Entradas: R$ 90,00").replace("Saldo final: R$ 60,00", "Saldo final: R$ 80,00")
    page = page.replace("Mercado Pago Instituição", "05-07-2026 Reembolso de Pix enviado 1002 R$ 20,00 R$ 80,00\nMercado Pago Instituição")
    with pytest.raises(PdfFinancialValidationError):
        parse_verified_statement([page])


def test_complete_printed_counter_sequence_and_unrelated_counters_are_allowed():
    from app.application.parsers.pdf.statement_completeness import validate_statement_completeness

    validate_statement_completeness(["Santander EXTRATO CONSOLIDADO INTELIGENTE\nPagina: 1/2", "Pagina: 2/2"])
    validate_statement_completeness(["Documento qualquer\nPagina: 3/8"])
