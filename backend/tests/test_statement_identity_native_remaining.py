"""Synthetic cases found while closing the remaining native PDF inventory."""

import pytest

from app.application.statement_identity import identify_statement
from tests import test_received_statements as receipt_fixtures

receipts = receipt_fixtures.receipts


@pytest.mark.parametrize("label", ["ClienteEMPRESA AURORA LTDA", "Cliente EMPRESA AURORA LTDA"])
def test_customer_label_without_separator_is_not_part_of_the_customer_name(label):
    source = identify_statement({"bank_code": "001"}, page_texts=[
        f"Extrato de Conta Corrente\n{label}\nAgência: 0001-2 Conta: 00123-4\nLançamentos\n"
        "Dia Documento Histórico Valor\n01/10/2026 PIX 10,00\nClienteOUTRA CONTRAPARTE"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["branch"] == "0001-2" and source["account_number"] == "00123-4"
    assert source["confidence"] == "source_account"


def test_company_starting_with_similar_word_keeps_its_original_name():
    source = identify_statement({"bank_code": "001"}, page_texts=[
        "Extrato de Conta Corrente\nCLIENTELA SERVICOS LTDA\nAgência: 0001 Conta: 00123-4\nData Histórico Valor"
    ])
    assert source["holder_name"] == "CLIENTELA SERVICOS LTDA"


@pytest.mark.parametrize("lines,expected", [
    ("PESSOA EXEMPLO SOCIEDADE INDIVIDUAL\nDE ADVOCACIA", "PESSOA EXEMPLO SOCIEDADE INDIVIDUAL DE ADVOCACIA"),
    ("SEU HORIZONTE COMERCIO DE PRODUTOS\nESPECIAIS LTDA", "SEU HORIZONTE COMERCIO DE PRODUTOS ESPECIAIS LTDA"),
])
def test_multiline_holder_before_tax_keeps_the_entire_printed_name(lines, expected):
    source = identify_statement({"bank_code": "323"}, page_texts=[
        f"EXTRATO DE CONTA\n{lines}\nCPF/CNPJ: 12.345.678/0001-90\nAgência: 0001 Conta: 00123-4\nData Histórico Valor"
    ])
    assert source["holder_name"] == expected
    assert source["holder_tax_id"] == "12345678000190"


def test_bank_brand_above_owner_is_not_joined_to_the_legal_name():
    source = identify_statement({"bank_code": "323"}, page_texts=[
        "EXTRATO DE CONTA\nMercado Pago\nEMPRESA AURORA LTDA\nCNPJ: 12.345.678/0001-90\n"
        "Agência: 0001 Conta: 00123-4\nData Histórico Valor"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"


def test_tax_cannot_consume_the_start_of_a_numeric_company_name():
    source = identify_statement({"bank_code": "341"}, page_texts=[
        "Agência 0001 Conta 00123-4 CNPJ 12.345.678/0001-12.345.678 EMPRESA AURORA\n"
        "Data Histórico Valor"
    ])
    assert source["holder_tax_id"] == ""


def test_invalid_tax_does_not_hide_a_readable_owner_or_create_a_strong_identity():
    source = identify_statement({"bank_code": "237"}, page_texts=[
        "Extrato Mensal\nEMPRESA AURORA LTDA | CNPJ: 012.345.678/0001-90\n"
        "Agência: 0001 Conta: 00123-4\nData Histórico Valor"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "" and source["confidence"] == "ambiguous"


def test_tax_check_digits_are_recovered_only_from_a_visible_header_continuation():
    source = identify_statement({"bank_code": "341"}, page_texts=[
        "Agência 0001 Conta 00123-4 CNPJ 12.345.678/0001-12.345.678 EMPRESA AURORA\n"
        "90\nData Histórico Valor"
    ])
    assert source["holder_name"] == "12.345.678 EMPRESA AURORA"
    assert source["holder_tax_id"] == "12345678000190"
    assert {ref["line"] for ref in source["references"]} >= {1, 2}


def test_unreadable_owner_tax_stays_pending_and_keeps_its_review_reason(receipts):
    service, owner, _ = receipts
    source = identify_statement({"bank_code": "237"}, page_texts=[
        "Extrato Mensal\nEMPRESA AURORA LTDA | CNPJ: 012.345.678/0001-90\n"
        "Agência: 0001 Conta: 00123-4\nData Histórico Valor"
    ])
    service.receive(receipt_fixtures.record(owner, source=source, days=30))
    page = service.dashboard(owner)
    assert page["clients"] == []
    assert page["pending"][0]["source"]["identity_issues"] == ["unreadable_holder_tax_id"]


def test_valid_tax_before_other_header_fields_is_not_an_unreadable_document():
    source = identify_statement({"bank_code": "208"}, page_texts=[
        "EMPRESA AURORA LTDA\nCNPJ: 12.345.678/0001-90 · Banco 208 · Agência 50 · Conta 00123-4\nData Histórico Valor"
    ])
    assert source["holder_tax_id"] == "12345678000190" and source["confidence"] == "strong"
    assert not source.get("identity_issues")


def test_concatenated_month_year_and_page_counter_is_not_a_customer_name():
    source = identify_statement({"bank_code": "341"}, page_texts=[
        "extrato mensal\nag 0001 cc 00123-4 fev 2026001|008\nData Histórico Valor"
    ])
    assert source["holder_name"] == "" and source["account_number"] == "00123-4"


def test_combined_agency_account_labels_keep_the_values_in_their_printed_roles():
    source = identify_statement({"bank_code": "001", "account_number": "0001-2"}, page_texts=[
        "Extratos - Poupança\n- POUPANÇA-OURO DIÁRIA51\n- POUPEX96\n"
        "Agência / Conta0001-2 / 00123-4\nData Histórico Valor"
    ])
    assert source["branch"] == "0001-2" and source["account_number"] == "00123-4"
    assert source["holder_name"] == ""


def test_tax_registration_and_check_digits_can_follow_a_visible_prefix_on_next_line():
    source = identify_statement({"bank_code": "341"}, page_texts=[
        "Agência 0001 Conta 00123-4 CNPJ 12.345.678EMPRESA AURORA LTDA\n/0001-90\nData Histórico Valor"
    ])
    assert source["holder_tax_id"] == "12345678000190" and source["holder_name"] == "EMPRESA AURORA LTDA"


def test_native_fallback_preserves_readable_name_and_tax_review_reason():
    from app.application.statement_identity import identify_native_statement
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

    header = ("Extrato Mensal", "EMPRESA AURORA LTDA | CNPJ: 012.345.678/0001-90",
              "Agência: 0001 Conta: 00123-4", "Data Histórico Valor")
    raw = generate_native_text_pdf((header,))
    source = identify_native_statement({"bank_code": "237"}, raw_bytes=raw, page_texts=[
        "Extrato Mensal\nAgência: 0001 Conta: 00123-4\nData Histórico Valor"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA" and source["holder_tax_id"] == ""
    assert source["confidence"] == "ambiguous"
    assert source["identity_issues"] == ["unreadable_holder_tax_id"]


def test_native_agency_account_columns_with_balance_do_not_conflict_with_parser_header():
    from app.application.statement_identity import identify_native_statement
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

    header = ("Extratos - Poupança", "Agência / Conta 0001-2 / 00123-4     Saldo: 10,00 C",
              "Data Histórico Valor")
    source = identify_native_statement({"bank_code": "001", "account_number": "0001-2"},
        raw_bytes=generate_native_text_pdf((header,)), page_texts=[
            "Extratos - Poupança\nAgência / Conta0001-2 / 00123-4\nData Histórico Valor"
        ])
    assert source["branch"] == "0001-2" and source["account_number"] == "00123-4"
    assert source["confidence"] == "missing"
