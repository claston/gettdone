"""Fiscal evidence never becomes a second bank movement."""

from decimal import Decimal

import pytest

from app.application.pro_financial_policy import classify_tax_payment, possible_duplicate_note, revenue_summary, suggest_invoice_matches
from app.application.pro_fiscal_documents import parse_fiscal_pages, parse_fiscal_xml, tax_identifier

ISSUER = "12345678000195"
RECIPIENT = "98765432000198"
KEY = "35261112345678000195550010000001231123456789"


def nfe(*, value="100.00", cfop="5102", recipient=RECIPIENT):
    return f"""<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe>
      <infNFe Id="NFe{KEY}" versao="4.00"><ide><nNF>123</nNF><serie>1</serie>
      <dhEmi>2026-11-01T10:00:00-03:00</dhEmi><tpNF>1</tpNF><finNFe>1</finNFe></ide>
      <emit><CNPJ>{ISSUER}</CNPJ><xNome>Empresa A</xNome></emit>
      <dest><CNPJ>{recipient}</CNPJ><xNome>Empresa B</xNome></dest>
      <det nItem="1"><prod><CFOP>{cfop}</CFOP></prod></det>
      <total><ICMSTot><vNF>{value}</vNF><vICMS>18.00</vICMS></ICMSTot></total>
      <cobr><fat><vLiq>{value}</vLiq></fat><dup><nDup>1</nDup><dVenc>2026-12-01</dVenc><vDup>{value}</vDup></dup></cobr>
      </infNFe></NFe><protNFe><infProt><cStat>100</cStat><chNFe>{KEY}</chNFe></infProt></protNFe></nfeProc>""".encode()


def nfse():
    return f"""<NFSe xmlns="http://www.sped.fazenda.gov.br/nfse" versao="1.00"><infNFSe Id="NFS{"1" * 50}">
      <nNFSe>7</nNFSe><dhProc>2026-11-03T09:00:00-03:00</dhProc><emit><CNPJ>{ISSUER}</CNPJ><xNome>Prestador</xNome></emit>
      <valores><vLiq>95.00</vLiq><vISSQN>5.00</vISSQN></valores>
      <DPS><infDPS><dhEmi>2026-11-02T09:00:00-03:00</dhEmi><dCompet>2026-10-31</dCompet>
      <prest><CNPJ>{ISSUER}</CNPJ></prest><toma><CNPJ>{RECIPIENT}</CNPJ><xNome>Tomador</xNome></toma>
      <valores><vServPrest><vServ>100.00</vServ></vServPrest><trib><tribMun><tpRetISSQN>2</tpRetISSQN></tribMun></trib></valores>
      </infDPS></DPS></infNFSe></NFSe>""".encode()


def movement(**extra):
    return {
        "id": "m1",
        "date": "2026-11-03",
        "amount": "100.00",
        "currency": "BRL",
        "description": f"PIX recebido {RECIPIENT}",
        "version": 1,
        "possible_duplicate": False,
        **extra,
    }


def test_xml_preserves_financial_roles_and_exact_values():
    note = parse_fiscal_xml(nfe())
    assert note["kind"] == "nfe"
    assert note["issuer"]["tax_id"] == ISSUER
    assert note["recipient"]["tax_id"] == RECIPIENT
    assert note["gross_amount"] == "100.00"
    assert note["taxes"]["ICMS"] == "18.00"
    assert note["operation"] == "sale"
    assert note["authority_status"] == "not_verified"
    assert note["installments"][0]["due_on"] == "2026-12-01"
    without_due_date = parse_fiscal_xml(nfe().replace(b"<dVenc>2026-12-01</dVenc>", b""))
    assert without_due_date["installments"][0]["due_on"] is None
    assert without_due_date["gross_amount"] == "100.00"


def test_nfse_keeps_gross_net_and_competence_separate():
    note = parse_fiscal_xml(nfse())
    assert (note["gross_amount"], note["net_amount"], note["competence_on"]) == ("100.00", "95.00", "2026-10-31")
    assert note["taxes"]["ISSQN"] == "5.00"
    assert (note["issued_on"], note["declared_on"]) == ("2026-11-03", "2026-11-02")


@pytest.mark.parametrize("purpose", ["1", "2"])
def test_nfse_adjustment_is_not_automatically_a_new_sale(purpose):
    note = parse_fiscal_xml(nfse().replace(b"</infDPS>", f"<IBSCBS><finNFSe>{purpose}</finNFSe></IBSCBS></infDPS>".encode()))
    assert note["review_required"] and note["operation"] == "other"


def test_foreign_currency_is_not_reported_as_brl_revenue():
    summary = revenue_summary([], [movement(currency="USD", classification={"category": "operating_receipt", "state": "confirmed"})])
    assert summary["operating_receipts"] is None
    assert summary["consolidation"] == "review_required"


def test_pdf_without_series_cannot_double_count_a_reviewed_xml():
    xml = parse_fiscal_xml(nfe())
    pdf = {**xml, "series": None, "number": "000123"}
    assert possible_duplicate_note(xml, pdf)
    assert not possible_duplicate_note(xml, {**pdf, "series": "2"})


@pytest.mark.parametrize(
    "raw",
    [
        b'<!DOCTYPE x [<!ENTITY a "bad">]><NFe>&a;</NFe>',
        b"<DPS/>",
        nfe(value="NaN"),
        nfe(value="1.001"),
        nfe().replace(b"Empresa A", b"A" * 601),
        nfe().replace(b"2026-11-01", b"2026-02-30"),
        b"<root>" + nfe() + nfe() + b"</root>",
    ],
)
def test_unsafe_ambiguous_or_non_invoice_xml_is_rejected(raw):
    with pytest.raises(ValueError):
        parse_fiscal_xml(raw)


def test_non_sale_and_return_are_not_revenue():
    assert parse_fiscal_xml(nfe(cfop="5915"))["operation"] == "other"
    assert parse_fiscal_xml(nfe().replace(b"<finNFe>1", b"<finNFe>4"))["operation"] == "return"


def test_cnpj_alphanumeric_is_preserved_without_repairing_invalid_input():
    assert tax_identifier("12.ABC.345/01DE-35") == "12ABC34501DE35"
    assert tax_identifier("12.345.*** /0001-95") is None


def test_pdf_keeps_missing_fields_and_original_pages_for_review():
    note = parse_fiscal_pages(
        [
            "NOTA FISCAL DE SERVIÇOS ELETRÔNICA\nNúmero da nota: 7\n"
            "PRESTADOR\nCNPJ: 12.345.678/0001-95\nTOMADOR\nCNPJ: 98.765.432/0001-98\n"
            "VALOR DOS SERVIÇOS: R$ 100,00\nVALOR LÍQUIDO: R$ 95,00"
        ]
    )
    assert note["gross_amount"] == "100.00"
    assert note["issued_on"] is None
    assert note["review_required"]
    assert note["source"]["fields"]["gross_amount"]["page"] == 1


def test_matching_explains_evidence_and_never_confirms_by_amount_alone():
    note = {**parse_fiscal_xml(nfe()), "id": "n1", "direction": "issued"}
    candidates = suggest_invoice_matches(note, [movement(), movement(id="m2", description="Crédito")])
    assert candidates[0]["movement_id"] == "m1"
    assert "counterparty_tax_id" in candidates[0]["evidence"]
    assert all(item["state"] == "suggested" for item in candidates)
    assert not suggest_invoice_matches(note, [movement(amount="-100.00"), movement(currency="USD")])


def test_tax_classification_requires_payment_context_not_company_name():
    assert classify_tax_payment("-123.00", "PAGAMENTO DARF")["category"] == "tax_federal_unknown"
    assert classify_tax_payment("-123.00", "PAGAMENTO DAS SIMPLES NACIONAL")["category"] == "tax_simples"
    assert classify_tax_payment("100.00", "Pagamento DAS") is None
    assert classify_tax_payment("-123.00", "PIX LOJA DAS FLORES") is None


def test_revenue_counts_documents_and_receipts_separately_and_exposes_pending():
    notes = [{**parse_fiscal_xml(nfe()), "direction": "issued", "review_required": False}]
    rows = [
        movement(classification={"category": "operating_receipt", "state": "confirmed"}),
        movement(id="m2", amount="20.00", classification={"category": "internal_transfer", "state": "confirmed"}),
        movement(id="m3", amount="30.00"),
    ]
    summary = revenue_summary(notes, rows)
    assert summary["documented_sales"] == "100.00"
    assert summary["operating_receipts"] == "100.00"
    assert summary["unclassified_inflows"] == "30.00"
    assert summary["coverage"] == "unknown"
    assert Decimal(summary["operating_receipts"]) != 200
    assert revenue_summary(notes, [movement(possible_duplicate=True)])["operating_receipts"] is None
