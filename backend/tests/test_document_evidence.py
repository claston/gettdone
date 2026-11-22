from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.document_evidence import parse_evidence_pages
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

RECEIPT = """COMPROVANTE PIX TRANSFERENCIA
20 jul. 2026, via SISPag
valor da transferencia R$ 4.189,59
ID da transacao E1234567890123456789012345678901
autenticacao do comprovante ABC123
"""
INVOICE = """NFS-e NOTA FISCAL DE SERVICOS ELETRONICA
PRESTADOR CNPJ: 12.345.678/0001-90
TOMADOR CNPJ: 98.765.432/0001-10
Numero da nota: 6
Data de emissao: 20/07/2026
Valor dos servicos R$ 4.189,59
Valor liquido R$ 4.000,00
"""


def test_receipt_records_value_and_provenance_without_cash_effect():
    document = parse_evidence_pages([RECEIPT])
    assert document["document_type"] == "bank_receipt"
    assert document["events"] == []
    amount = next(o for o in document["observations"] if o["amounts"])
    assert amount["amounts"] == {"value": "4189.59"}
    assert amount["source_refs"] == [{"page": 1, "line": 3}]
    assert "ofx" not in document["export"]["available_formats"]


def test_ambiguous_receipt_keeps_both_values_for_review():
    document = parse_evidence_pages([RECEIPT + "valor da transferencia R$ 5.000,00"])
    assert document["metadata"]["review_required"]
    assert len([o for o in document["observations"] if o["amounts"]]) == 2


def test_invoice_keeps_gross_net_and_not_verified():
    document = parse_evidence_pages([INVOICE])
    assert document["document_type"] == "fiscal_invoice"
    assert document["metadata"]["authority_status"] == "not_verified"
    assert document["metadata"]["payment_status"] == "not_verified"
    amounts = {k: v for o in document["observations"] for k, v in o["amounts"].items()}
    assert amounts == {"gross": "4189.59", "net": "4000.00"}
    assert document["events"] == []


def test_statement_with_receipt_annex_still_uses_statement_parser():
    assert parse_evidence_pages(["EXTRATO CONTA CORRENTE\nDATA HISTORICO VALOR SALDO\n01/07/2026 PIX -10,00 90,00", RECEIPT]) is None


def test_receipt_and_invoice_pipeline_have_no_bank_rows_or_balance_checks():
    pipeline = build_default_conversion_pipeline()
    for text, kind in [(RECEIPT, "bank_receipt"), (INVOICE, "fiscal_invoice")]:
        raw = generate_native_text_pdf((tuple(text.splitlines()),))
        result = pipeline.run(filename="documento.pdf", raw_bytes=raw, analysis_id="an_evidence")
        data = result.analysis_data
        assert data.document_type == kind
        assert data.financial_document["document_type"] == kind
        assert data.preview_transactions == []
        assert data.net_total == 0
        assert data.opening_balance is None and data.closing_balance is None
        assert data.statement_balance_check is None
        assert len(data.financial_document["source_document_sha256"]) == 64
