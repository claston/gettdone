"""Sales are receivables, including approved sales; settlement is not printed."""
import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError, PdfFinancialValidationError
from app.application.financial_document_export import assert_financial_export


def sales_report():
    return """Relatório de Vendas
Período 01/06/2026 a 30/06/2026
Data da Transação  Adquirente  Estabelecimento  Código da Transação  Código NSU  Tipo de Transação  Status
Valor bruto (R$)  Taxa (R$)  Valor líquido (R$)
30/06/2026 18:12  Pagbank  -  SALE-001  100001  Crédito Visa  Aprovada  R$ 100,00  R$ 5,00  R$ 95,00
30/06/2026 16:30  Pagbank  -  SALE-002  -  Boleto  Aguardando  R$ 50,00  R$ 0,00  R$ 50,00
                                              Pagamento
Valor Líquido do dia: R$ 145,00
Página 1 de 1"""


def test_pagbank_sales_keep_all_amounts_status_reference_and_no_cash_effect():
    result = pdf_parser._parse_pdf_transactions_from_page_texts([sales_report()])
    document = result.financial_document
    assert document["document_type"] == "acquirer_sales_report"
    assert len(document["events"]) == 2
    assert all(e["cash_effect"] is None for e in document["events"])
    event = next(e for e in document["events"] if e["reference"] == "SALE-002")
    assert event["status"] == "pending"
    assert event["attributes"]["printed_status"] == "Aguardando Pagamento"
    assert event["amounts"] == {"gross": "50.00", "fee": "0.00", "net": "50.00"}
    assert all(v["status"] == "passed" for v in document["validations"])
    assert all(t.type == "non_cash" for t in result.transactions)
    with pytest.raises(InvalidFileContentError):
        assert_financial_export(document, "ofx")


def test_sales_report_keeps_divergence_visible_without_changing_source_amount():
    text = sales_report().replace("R$ 95,00", "R$ 94,00")
    result = pdf_parser._parse_pdf_transactions_from_page_texts([text])
    assert any(v["status"] == "failed" for v in result.financial_document["validations"])
    assert next(e for e in result.financial_document["events"] if e["reference"] == "SALE-001")["amounts"]["net"] == "94.00"


@pytest.mark.parametrize("old,new", [("Página 1 de 1", "Página 1 de 2"), ("R$ 5,00  R$ 95,00", "R$ 95,00"),
                                     ("30/06/2026 16:30", "31/06/2026 16:30"), ("30/06/2026 16:30", "01/07/2026 16:30")])
def test_sales_report_rejects_missing_pages_columns_and_invalid_dates(old, new):
    pages = [sales_report().replace(old, new)]
    if old.startswith("Página"):
        result = pdf_parser._parse_pdf_transactions_from_page_texts(pages)
        assert result.parse_metrics["partial_statement"] == 1
    else:
        with pytest.raises(PdfFinancialValidationError):
            pdf_parser._parse_pdf_transactions_from_page_texts(pages)
