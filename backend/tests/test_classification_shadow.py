"""Deterministic suggestions never become financial authority."""

import pytest
from test_pro_fiscal_documents import nfe

from app.application.classification_shadow.policy import classify_movement, extract_invoice_items


@pytest.mark.parametrize("amount,description,category", [
    ("-10.00", "TARIFA MANUTENCAO CONTA", "bank_fee"),
    ("-10.00", "PAGAMENTO DARF", "tax_federal_unknown"),
    ("-10.00", "PAGAMENTO DAS", "tax_simples"),
    ("-10.00", "PIX LOJA DAS FLORES", None),
    ("10.00", "PIX RECEBIDO", None),
    ("10.00", "ESTORNO TARIFA", None),
    ("-10.00", "TRANSFERENCIA", None),
    ("-10.00", "PAGAMENTO DARF ISS", None),
    ("-10.00", "TARIFA JUROS", None),
])
def test_explicit_rules_abstain_on_ambiguity(amount, description, category):
    original = {"id": "movement", "amount": amount, "description": description}
    assert classify_movement(original)["category"] == category
    assert original == {"id": "movement", "amount": amount, "description": description}


def invoice():
    return nfe().replace(b"<CFOP>5102</CFOP>", b"""<cProd>A1</cProd><xProd>Arroz</xProd><NCM>10063021</NCM>
    <CFOP>5102</CFOP><qCom>2.0000</qCom><uCom>KG</uCom><vProd>100.00</vProd>""").replace(
        b"</prod></det>", b"</prod><imposto><IBSCBS><CST>200</CST><cClassTrib>200003</cClassTrib></IBSCBS></imposto></det>"
    )


def test_xml_items_preserve_codes_precision_and_provenance():
    result = extract_invoice_items(invoice())
    assert result["reference_date"] == "2026-11-01"
    assert result["items"][0]["ncm"] == "10063021"
    assert result["items"][0]["quantity"] == "2.0000"
    assert result["items"][0]["cClassTrib"] == "200003"
    assert result["items"][0]["source_ref"] == "infNFe/det[@nItem='1']"


def test_missing_code_is_preserved_and_pdf_is_not_ocr_input():
    assert extract_invoice_items(nfe())["items"][0]["ncm"] is None
    with pytest.raises(ValueError):
        extract_invoice_items(b"%PDF-1.7 scanned")


def test_duplicate_item_identity_cannot_silently_drop_a_line():
    raw = invoice()
    start, end = raw.index(b"<det "), raw.index(b"</det>") + len(b"</det>")
    with pytest.raises(ValueError, match="item"):
        extract_invoice_items(raw[:end] + raw[start:end] + raw[end:])
