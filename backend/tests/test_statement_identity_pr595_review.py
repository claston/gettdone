"""Review regressions: column boundaries and investment-holder/product roles."""

import pytest

from app.application.statement_identity import identify_native_statement, identify_statement
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests import test_received_statements as receipt_fixtures

receipts = receipt_fixtures.receipts


def recognize(header, representation):
    pages = [header + "\nData Historico Valor\n01/09 PIX 10,00"]
    if representation == "native_pdf":
        return identify_native_statement({"bank_code": "001"}, page_texts=pages,
            raw_bytes=generate_native_text_pdf((tuple(pages[0].splitlines()),)))
    return identify_statement({"bank_code": "001"}, page_texts=pages, representation=representation)


@pytest.mark.parametrize("representation", ["native_pdf", "ocr"])
@pytest.mark.parametrize("name", ["EMPRESA  AURORA LTDA", "EMPRESA   AURORA  LTDA"])
def test_aligned_columns_keep_internal_name_spaces_and_correct_account(receipts, representation, name):
    header = ("Extrato de Conta Corrente\n"
              f"{'Nome':<32}{'CNPJ':<24}{'Agencia':<14}Conta\n"
              f"{name:<32}{'12.345.678/0001-90':<24}{'0001':<14}00123-4")
    source = recognize(header, representation)
    assert " ".join(source["holder_name"].split()) == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["branch"] == "0001" and source["account_number"] == "00123-4"
    assert source["confidence"] == "strong"
    assert any(ref["page"] == 1 and ref["line"] == 3 for ref in source["references"])
    service, owner, _ = receipts
    item = receipt_fixtures.record(owner, source=source, days=30)
    service.receive(item)
    page = service.dashboard(owner, now=item.created_at)
    assert page["pending"] == [] and len(page["clients"]) == 1
    assert " ".join(page["clients"][0]["name"].split()) == "EMPRESA AURORA LTDA"
    account = service.catalog(owner)["accounts"][0]
    assert account["branch"] == "0001" and account["account_number"] == "00123-4"


@pytest.mark.parametrize("representation", ["native_pdf", "ocr"])
@pytest.mark.parametrize("values", [
    "EMPRESA  AURORA LTDA  12.345.678/0001-90  0001  00123-4",
    "EMPRESA AURORA LTDA  12.345.678/0001-90  0001  00123-4  00987-6",
    "EMPRESA AURORA LTDA  0001  12.345.678/0001-90  00123-4",
    "EMPRESA  12345678901  0001  00123-4",
])
def test_unaligned_extra_cells_cannot_create_customer_or_account(receipts, representation, values):
    header = ("Extrato de Conta Corrente\n"
              f"{'Nome':<32}{'CNPJ':<24}{'Agencia':<14}Conta\n{values}")
    source = recognize(header, representation)
    assert source["confidence"] == "ambiguous"
    assert "ambiguous_header_columns" in source["identity_issues"]
    service, owner, _ = receipts
    item = receipt_fixtures.record(owner, source=source, days=30)
    service.receive(item)
    assert service.dashboard(owner, now=item.created_at)["clients"] == []
    assert service.catalog(owner)["accounts"] == []


@pytest.mark.parametrize("representation", ["native_pdf", "ocr"])
@pytest.mark.parametrize("period_first", [False, True])
def test_fund_holder_survives_investment_title_and_origin_account(representation, period_first):
    owner = "FUNDO MUNICIPAL DE SAUDE | CNPJ: 12.345.678/0001-90"
    period = "Mes/ano referencia SETEMBRO/2026"
    header = ("Extratos - Investimentos Fundos - Mensal\nAgencia: 0001-2\nConta: 00123-4\n"
              + "\n".join([period, owner] if period_first else [owner, period]))
    source = recognize(header, representation)
    assert source["holder_name"] == "FUNDO MUNICIPAL DE SAUDE"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["confidence"] == "strong"


@pytest.mark.parametrize("representation", ["native_pdf", "ocr"])
def test_fund_holder_and_distinct_invested_fund_keep_separate_roles(representation):
    header = ("Extratos - Investimentos Fundos - Mensal\nAgencia: 0001-2\nConta: 00123-4\n"
              "FUNDO MUNICIPAL DE SAUDE | CNPJ: 12.345.678/0001-90\nMes/ano referencia SETEMBRO/2026\n"
              "FUNDO RF EXEMPLO - CNPJ: 98.765.432/0001-10")
    source = recognize(header, representation)
    assert source["holder_name"] == "FUNDO MUNICIPAL DE SAUDE"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["confidence"] == "strong"


@pytest.mark.parametrize("representation", ["native_pdf", "ocr"])
def test_uncertain_body_columns_do_not_contaminate_holder_review(representation):
    header = ("Titular: EMPRESA AURORA LTDA CNPJ: 12.345.678/0001-90\nAgencia: 0001 Conta: 00123-4\n"
              "Data Historico Valor\n01/09 PIX 10,00\nNome        CNPJ       Agencia       Conta\n"
              "TERCEIRO  EXEMPLO  98.765.432/0001-10  9999  00987-6")
    source = recognize(header, representation)
    assert source["holder_name"] == "EMPRESA AURORA LTDA"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["confidence"] == "strong" and not source.get("identity_issues")


@pytest.mark.parametrize("representation", ["native_pdf", "ocr"])
def test_distinct_fund_holder_pairs_remain_ambiguous(representation):
    header = ("Extratos - Investimentos Fundos - Mensal\nAgencia: 0001-2\nConta: 00123-4\n"
              "FUNDO MUNICIPAL DE SAUDE | CNPJ: 12.345.678/0001-90\nMes/ano referencia SETEMBRO/2026\n"
              "FUNDO MUNICIPAL DE EDUCACAO | CNPJ: 98.765.432/0001-10")
    source = recognize(header, representation)
    assert source["confidence"] == "ambiguous"
    assert source["holder_name"] == source["holder_tax_id"] == ""
