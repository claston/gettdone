"""Synthetic owner/issuer headers; no customer identifiers or OCR responses."""

import pytest

from app.application.bank_catalog import list_bank_options, resolve_bank_code_from_name
from app.application.bank_identity import resolve_bank_name
from app.application.bank_resolver import resolve_bank_code
from app.application.statement_identity import identify_statement
from tests import test_received_statements as receipt_fixtures

receipts = receipt_fixtures.receipts
record = receipt_fixtures.record

OWNER = "EMPRESA AURORA LTDA"
FIELDS = "CNPJ 12.345.678/0001-90 Agência: 0001 Conta: 0012345-6"
BODY = "Data Movimentações Valor\n01/01/2026 PIX RECEBIDO TERCEIRO 10,00"


def header(*, issuer="ASAAS", owner=OWNER, logo_between=True):
    lines = [owner, issuer] if logo_between else [issuer, owner]
    return "\n".join([line for line in lines if line] + [FIELDS, "Período: 01/01/2026 a 31/01/2026", BODY])


def test_asaas_has_an_institution_code_and_selectable_name():
    assert resolve_bank_code_from_name("Asaas") == "461"
    assert resolve_bank_code(layout_inference_name="asaas_extrato_conta_digital_movimentacoes_v1") == "461"
    assert resolve_bank_name(extracted_text=header()) == "Asaas"
    assert any(row["label"] == "Asaas (461)" for row in list_bank_options())


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("logo_between", [True, False])
def test_institution_logo_is_not_the_holder_between_name_and_account_tax(representation, logo_between):
    source = identify_statement({"bank_code": "461"}, page_texts=[header(logo_between=logo_between)] * 2, representation=representation)
    assert source["holder_name"] == OWNER
    assert source["holder_tax_id"] == "12345678000190"
    assert source["bank_code"] == "461" and source["branch"] == "0001"
    assert source["account_number"] == "0012345-6"
    assert source["confidence"] == "strong" and len(source["accounts"]) == 1
    assert source["representation"] == representation
    assert {"page": 1, "line": 1 if logo_between else 2} in source["references"]


@pytest.mark.parametrize("issuer,code", [("Nubank", "260"), ("Itaú", "341")])
def test_logo_handling_is_shared_with_other_institutions(issuer, code):
    source = identify_statement({"bank_code": code}, page_texts=[header(issuer=issuer)], representation="ocr")
    assert source["holder_name"] == OWNER and source["holder_tax_id"] == "12345678000190"


def test_missing_holder_does_not_create_a_customer_named_after_the_issuer(receipts):
    service, owner, _ = receipts
    source = identify_statement({"bank_code": "461"}, page_texts=[header(owner="")], representation="ocr")
    assert source["holder_name"] == ""
    service.receive(record(owner, source=source, days=30))
    assert service.dashboard(owner)["clients"] == []


def test_issuer_tax_without_same_line_origin_account_is_not_assigned_to_preceding_company():
    source = identify_statement(
        {"bank_code": "461"},
        page_texts=[OWNER + "\nASAAS\nCNPJ: 98.765.432/0001-10\nAgência: 0001 Conta: 0012345-6\n" + BODY],
        representation="ocr",
    )
    assert source["holder_name"] == source["holder_tax_id"] == ""


def test_explicit_holder_can_be_an_institution_and_partial_brand_names_are_preserved():
    for owner in ("Titular: Asaas", "ASAAS COMERCIO LTDA"):
        source = identify_statement({"bank_code": "461"}, page_texts=[header(owner=owner, logo_between=False)])
        assert source["holder_name"] == owner.removeprefix("Titular: ")
    source = identify_statement(
        {"bank_code": "461"}, page_texts=["Nome / Razão Social CPF/CNPJ\nAsaas 12.345.678/0001-90\n" + BODY]
    )
    assert source["holder_name"] == "Asaas" and source["holder_tax_id"] == "12345678000190"


def test_recognized_holder_creates_a_client_and_asaas_account_in_pro(receipts):
    service, owner, other = receipts
    source = identify_statement({"bank_code": "461"}, page_texts=[header()], representation="ocr")
    service.receive(record(owner, source=source, days=30))
    client = service.dashboard(owner)["clients"][0]
    assert client["name"] == OWNER
    assert service.catalog(owner)["accounts"][0]["bank_code"] == "461"
    assert service.dashboard(other)["clients"] == []


def test_header_issuer_beats_other_institutions_in_movements():
    assert resolve_bank_name(extracted_text=header() + "\n02/01/2026 PIX SANTANDER -5,00") == "Asaas"
    assert resolve_bank_name(extracted_text="Extrato de conta corrente\n" + BODY + "\n02/01/2026 PIX ASAAS -5,00") is None
