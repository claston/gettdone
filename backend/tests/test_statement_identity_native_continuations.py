"""Synthetic header/continuation shapes found in the second native-only audit."""

import pytest

from app.application.statement_identity import identify_statement


@pytest.mark.parametrize("tax", ["•••.222.333-••", "***.222.333-**", "12.345.678/0001-90"])
@pytest.mark.parametrize("order", ["values_first", "labels_first"])
def test_column_header_owns_account_and_never_uses_counterparties_on_later_pages(tax, order):
    label = "CNPJ" if "/" in tax else "CPF"
    columns = (f"{tax} 0001 {label} Agência Conta" if order == "values_first"
               else f"{label} {tax} Agência 0001 Conta")
    header = (
        f"EMPRESA AURORA LTDA\n{columns}\n00123456-7\n"
        "01 DE OUTUBRO DE 2026 a 28 DE OUTUBRO DE 2026 VALORES EM R$\n"
    )
    pages = [header + "Movimentações\n01 OUT 2026 Total de entradas + 100,00\n"
             "Transferência recebida pelo Pix CONTRAPARTE LTDA CNPJ 98.765.432/0001-10\n"
             "Agência: 9999 Conta: 987654-3\n",
             header + "Transferência enviada pelo Pix OUTRA CONTRAPARTE\n"
             "Agência: 8888 Conta: 555555-0\n02 OUT 2026 Total de saídas - 10,00\n"]
    source = identify_statement({"bank_code": "260"}, page_texts=pages)
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == ("12345678000190" if label == "CNPJ" else "")
    assert source["branch"] == "0001" and source["account_number"] == "00123456-7"
    assert len(source["accounts"]) == 1
    assert source["confidence"] == ("strong" if label == "CNPJ" else "source_account")
    assert {ref["page"] for ref in source["references"]} == {1, 2}


def test_true_different_owner_accounts_in_repeated_column_headers_stay_separate():
    header = "EMPRESA AURORA LTDA\n•••.222.333-•• 0001 CPF Agência Conta\n{}\nVALORES EM R$\n"
    source = identify_statement({"bank_code": "260"}, page_texts=[header.format("00123456-7"), header.format("00987654-3")])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "" and source["account_number"] == ""
    assert {item["account_number"] for item in source["accounts"]} == {"00123456-7", "00987654-3"}
    assert source["confidence"] != "source_account"


def test_postal_address_before_origin_account_is_not_a_customer_name():
    source = identify_statement({"bank_code": "237"}, page_texts=[
        "Extrato Unificado - Pessoa Jurídica\nEMPRESA AURORA LTDA\nRUA DAS FLORES 100\n"
        "CENTRO\n12345-678 CIDADE EXEMPLO SP\nAgência\n0001-2\nConta\n00123-4\nData Histórico Valor"
    ])
    assert source["holder_name"] == "" and source["confidence"] == "missing"
    assert source["account_number"] == "00123-4"


@pytest.mark.parametrize("suffix", ["LTDA", "LT  D  A"])
def test_explicit_account_holder_can_wrap_a_legal_suffix_onto_the_next_line(suffix):
    source = identify_statement({"bank_code": "756"}, page_texts=[
        "Cooperativa: 0007-1\nConta: 00123-4 / EMPRESA AURORA COMERCIO DE ALIMENTOS\n"
        f"{suffix}\nPERÍODO: 01/10/2026 - 28/10/2026\nHISTÓRICO DE MOVIMENTAÇÃO"
    ])
    assert source["holder_name"] == "EMPRESA AURORA COMERCIO DE ALIMENTOS LTDA"
    assert source["account_number"] == "00123-4"
    assert any(ref["line"] == 3 for ref in source["references"])


def test_bank_code_and_issuer_before_agency_do_not_conflict_with_owner_tax():
    source = identify_statement({"bank_code": "290"}, page_texts=[
        "Extrato da conta\nEMPRESA AURORA LTDA\nCNPJ: 12.345.678/0001-90\n"
        "290 - INSTITUICAO EMISSORA S/A\nAgência 0001\nConta 00123-4\nData Descrição Valor"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190" and source["confidence"] == "strong"


def test_formatted_tax_next_to_name_and_complete_origin_account_is_owner_evidence():
    source = identify_statement({"bank_code": "336"}, page_texts=[
        "Extrato exportado no dia 01/11/2026\nEMPRESA AURORA • 12.345.678/0001-90\n"
        "Agência: 1 • Conta: 001234567\nData\nlançamento\n01/10/2026 PIX -10,00"
    ])
    assert source["holder_name"] == "EMPRESA AURORA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["account_number"] == "001234567" and source["confidence"] == "strong"


def test_concatenated_account_label_is_never_a_holder_name():
    from app.application.statement_identity import _holder_candidate

    assert _holder_candidate("conta00123-4Minha") == ""


def test_generic_name_on_a_body_only_continuation_is_not_another_holder():
    source = identify_statement({"bank_code": "041"}, page_texts=[
        "AGENCIA: 0001\nCONTA: 00123-4\nNOME: EMPRESA AURORA LTDA\nDIA HISTORICO DOCUMENTO VALOR",
        "NOME: PRIMEIRA CONTRAPARTE\n12 PIX 123 10,00\nNOME: SEGUNDA CONTRAPARTE\n",
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["account_number"] == "00123-4" and source["confidence"] == "source_account"


def test_internal_alphanumeric_account_reference_is_not_a_numeric_bank_account():
    source = identify_statement({"account_number": "0000"}, page_texts=[
        "Filtros utilizados:\n- Conta Corrente: 0000/SISTEMA-EXEMPLO - SISTEMA-EXEMPLO /\n"
        "- Período Inicio: 01/10/2026\nData Movimento Histórico Valor"
    ])
    assert source["account_number"] == "" and source["holder_name"] == ""
    assert source["account_reference"] == "0000/SISTEMA-EXEMPLO - SISTEMA-EXEMPLO /"


def test_financial_report_owner_and_labeled_header_account_do_not_require_ocr():
    source = identify_statement({}, page_texts=[
        "Relatório de movimentações EMPRESA AURORA LTDA - CNPJ: 12.345.678/0001-90\n"
        "INSTITUICAO EMISSORA - 0001 - 00123-4\n01 Out, 2026 - 31 Out, 2026 Valor em R$\n"
        "Data Hora Tipo de transação Nome Detalhe Valor (R$)\n01/10/2026 PIX 10,00"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["branch"] == "0001" and source["account_number"] == "00123-4"
    assert source["bank_code"] == ""


def test_account_product_description_after_holder_label_is_not_a_person_name():
    from app.application.statement_identity import _holder_candidate

    assert _holder_candidate("123.456.789-0 CONTA CORRENTE PESSOA JURIDICA") == ""


def test_contractor_name_and_document_are_explicit_owner_fields():
    source = identify_statement({}, page_texts=[
        "Conta Digital | Extrato\nContratante:\nEMPRESA AURORA LTDA\nCNPJ:\n12.345.678/0001-90\n"
        "Ag: 0001\nConta: 00123-4\nData Histórico Valor"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"


def test_document_initial_holder_tax_does_not_require_a_printed_account_number():
    source = identify_statement({}, page_texts=[
        "EMPRESA AURORA LTDA\nCNPJ: 12.345.678/0001-90\nSAC: 0800 000 0000\n"
        "Emitido por INSTITUICAO EMISSORA\nINSTITUICAO DE PAGAMENTO S.A. CNPJ 98.765.432/0001-10\n"
        "Extrato\nPeríodo: 01/10/2026 a 31/10/2026\nData Descrição Valor"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190" and source["account_number"] == ""


def test_initial_issuer_legal_name_and_tax_never_become_an_owner_without_account():
    source = identify_statement({}, page_texts=[
        "BANCO EXEMPLO S.A.\nCNPJ: 98.765.432/0001-10\nExtrato\nData Descrição Valor"
    ])
    assert source["holder_name"] == source["holder_tax_id"] == ""


def test_reordered_report_title_between_owner_and_account_is_not_a_second_holder():
    source = identify_statement({}, page_texts=[
        "EMPRESA AURORA LTDA - CNPJ: 12.345.678/0001-90\nRelatório de movimentações\n"
        "INSTITUICAO EMISSORA - 0001 - 00123-4\nData Hora Tipo de transação Nome Detalhe Valor"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190" and source["account_number"] == "00123-4"
