"""Synthetic header shapes from the November audit; no customer identities or PDFs."""

import pytest

from app.application.statement_identity import identify_native_statement, identify_statement
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("header", [
    "Extrato de Conta\nConta Corrente\nNome                         CNPJ                       Posição\n"
    "EMPRESA AURORA LTDA           12.345.678/0001-90         Setembro/2026\n"
    "Agência (prefixo/dv)          GS          Conta nº/dv        Data da abertura\n"
    "0001-2                       16          00123-4            01.01.2020",
    "Extrato de Conta Corrente\nDados da Conta\nNome                       Documento\n"
    "EMPRESA AURORA LTDA         12.345.678/0001-90\n"
    "Instituição                         Agência       Conta\n"
    "Instituição de Pagamento S.A.        0001          00123-4",
    "EMPRESA AURORA LTDA CNPJ                  Agência 0001 Conta 00123-4\n12.345.678/0001-90",
    "Extrato da Conta Digital\nCNPJ: 12.345.678/0001-90 Nome: EMPRESA AURORA LTDA\n"
    "Banco: 481 / Agência: 0001 / Conta: 00123-4",
    "EMPRESA AURORA LTDA\nnu\nCNPJ 12.345.678/0001-90 Agência 0001 Conta 00123-4",
    "290 - PagSeguro Internet S/A\nEMPRESA AURORA LTDA\nPagBank\nAgência 0001\n"
    "CNPJ: 12.345.678/0001-90\nConta 00123-4\nExtrato da conta",
    "Dados da Conta\nNome\nEMPRESA AURORA LTDA\nDocumento\n12 .345.678/0001-90\nAgência\n0001\nConta\n00123-4",
])
def test_complete_printed_holder_survives_columns_wrapping_and_interleaved_logo(header, representation):
    source = identify_statement({}, page_texts=[header + "\nData Histórico Valor\n01/09 PIX CONTRAPARTE 10,00"],
                                representation=representation)
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["account_number"] == "00123-4"
    assert source["confidence"] == "strong"
    assert source["references"]


def test_investment_fund_tax_is_not_a_customer_tax_or_name():
    source = identify_statement({"bank_code": "001"}, page_texts=[
        "Extratos - Investimentos Fundos - Mensal\nCliente\nAgência 0001-2\n"
        "Conta 00123-4 EMPRESA AURORA LTDA\nMês/ano referência SETEMBRO/2026\n"
        "BB RF CP Automático - CNPJ: 98.765.432/0001-10\nData Histórico Valor\n01/09 Resgate 10,00"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == ""
    assert source["confidence"] == "source_account"
    assert source["branch"] == "0001-2"


@pytest.mark.parametrize("header", [
    "Cliente                        Cliente Corrente\nMUNICIPIO EXEMPLO               22/001.234-0",
    "Cliente\nMUNICIPIO EXEMPLO\nCliente Corrente\n22/001.234-0",
])
def test_current_customer_column_label_is_not_a_name(header):
    source = identify_statement({}, page_texts=["EXTRATO DE CONTA CORRENTE\n" + header + "\nData Histórico Valor"])
    assert source["holder_name"] == "MUNICIPIO EXEMPLO"
    assert source["holder_tax_id"] == "" and source["confidence"] == "name"


@pytest.mark.parametrize("header,name", [
    ("Sistema de Automação\nEMPRESA AURORA-EXTRATO DE CONTA CORRENTE Conta:00123-4\nAgência:0001", "EMPRESA AURORA"),
    ("COOPERATIVA REGIONAL EXTRATO DE CONTA CORRENTE\nEMPRESA AURORA LTDA\n00123-4\nRUA EXEMPLO, 10", "EMPRESA AURORA LTDA"),
    ("Extrato\nEMPRESA AURORA - **.***.\nPeríodo de 01/09/2026 a 30/09/2026 Coop: 0123 - AG: 0001 - Conta: 00123-4\n"
     "Solicitado por OUTRA PESSOA - **.***.123/0001-**", "EMPRESA AURORA"),
    ("Agência:              Conta:              Nome:\n"
     "0001                  00123-4             EMPRESA AURORA LTDAJANEIRO/2020 012345678", "EMPRESA AURORA LTDA"),
])
def test_readable_name_is_preserved_without_inventing_missing_tax(header, name):
    source = identify_statement({}, page_texts=[header + "\nData Histórico Valor"])
    assert source["holder_name"] == name
    assert source["holder_tax_id"] == ""


def test_displaced_monthly_header_preserves_name_and_malformed_tax_review():
    source = identify_statement({}, page_texts=[
        "Agência: 0001 Conta: 00123-4\nData Histórico Valor\n01/09 PIX 10,00\n"
        "Extrato Mensal / Por Período\nEMPRESA AURORA LTDA | CNPJ: 012.345.678/0001-90\n"
        "Nome do usuário: OPERADOR EXEMPLO\nData da operação: 01/10/2026"
    ])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == ""
    assert source["confidence"] == "ambiguous"
    assert source["identity_issues"] == ["unreadable_holder_tax_id"]


def test_reversed_cooperative_columns_do_not_invent_a_conflicting_account():
    printed = ("Associado: EMPRESA AURORA LTDA\nCooperativa: 0123 Conta Corrente: 001234 "
               "Impresso em 2026-11-12 09:28:17\nData Histórico Valor")
    captured = ("2026-11-12 09:28:17 Impresso em 001234 Conta Corrente: 0123Cooperativa:\n"
                "Associado:\nEMPRESA AURORA LTDA\nData Histórico Valor")
    source = identify_native_statement({"bank_code": "748"},
        raw_bytes=generate_native_text_pdf((tuple(printed.splitlines()),)), page_texts=[captured])
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["account_number"] == "001234" and source["cooperative"] == "0123"
    assert source["confidence"] == "source_account"


@pytest.mark.parametrize("header", [
    "EXTRATO DE CONTA\nAgência: 0001 Conta: 00123-4\nData Histórico Valor\n"
    "Nome                       Documento\nCONTRAPARTE LTDA           98.765.432/0001-10",
    "EMPRESA AURORA LTDA\nBanco do Brasil\nCNPJ: 98.765.432/0001-10\nAgência: 0001 Conta: 00123-4",
    "EXTRATO DE CONTA\nSolicitado por OPERADOR EXEMPLO\nCNPJ: 98.765.432/0001-10\nAgência: 0001 Conta: 00123-4",
])
def test_counterparty_issuer_and_operator_are_not_promoted_to_holder(header):
    source = identify_statement({}, page_texts=[header])
    assert source["holder_tax_id"] == ""
    assert source["holder_name"] not in {"CONTRAPARTE LTDA", "Solicitado por OPERADOR EXEMPLO"}


def test_true_conflicts_still_require_review():
    source = identify_statement({}, page_texts=[
        f"Titular: {name}\nCNPJ: {tax}\nAgência: 0001 Conta: {account}\nData Histórico Valor"
        for name,tax,account in [("EMPRESA AURORA LTDA","12.345.678/0001-90","00123-4"),
                                 ("OUTRA EMPRESA LTDA","98.765.432/0001-10","00987-6")]
    ])
    assert source["confidence"] == "ambiguous"
    assert source["holder_name"] == source["holder_tax_id"] == ""


def test_fund_can_be_the_actual_account_holder():
    source = identify_statement({}, page_texts=[
        "EXTRATO DE CONTA CORRENTE\nFUNDO MUNICIPAL EXEMPLO | CNPJ: 12.345.678/0001-90\n"
        "Agência: 0001 Conta: 00123-4\nData Histórico Valor"
    ])
    assert source["holder_name"] == "FUNDO MUNICIPAL EXEMPLO"
    assert source["holder_tax_id"] == "12345678000190"
