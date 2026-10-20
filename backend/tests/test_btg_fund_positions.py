import pytest

from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import _parse_pdf_transactions_from_page_texts


def fund_pages():
    return [
        "Extrato da\nConta Investimento\nPeríodo de 01/01/25 a 31/01/25\nEmitido em 03/02/25 22:35\n"
        "Banco: 208 BTG Pactual\nAgência: 0001\nConta investimento: 00012345\nFundo de Investimento",
        "Sumário\nDistribuição\n31/12/24\n31/12/24\n31/01/25\n31/01/25\n"
        "Fundos de Investimento 100,00 99,00 110,00 108,00\nTotal 100,00 99,00 110,00 108,00",
        "Fundo de Investimento\nPosição Portfólio de fundos\nQuantidade de Cotas Cotação Atual R$\n"
        "FUNDO SINTÉTICO\n31/01/25 99,00 100,00000 1,1000000 110,00 2,00\n-\n108,00 9,00\n"
        "Total em fundos 110,00 2,00 - 108,00 9,00",
        "Fundo de Investimento\nDetalhamento › FUNDO SINTÉTICO - 00.000.000/0001-00\n"
        "Data Compra Quantidade de Cotas Cotação Compra R$ Valor de Compra Ajustado R$\n"
        "15/10/24 40,00000 1,0000000 40,00 44,00 1,00 - 43,00\n"
        "01/11/24 60,00000 1,0000000 60,00 66,00 1,00 - 65,00\n"
        "Total 100,00000 110,00 2,00\n-\n108,00",
        "Conta corrente\nPosição\nData Saldo R$\n31/01/25 -",
    ]


def test_btg_positions_and_historical_lots_never_become_bank_transactions():
    result = _parse_pdf_transactions_from_page_texts(fund_pages())
    report = result.financial_document
    assert result.transactions == []
    assert report["origin"] == "BTG Pactual" and report["events"] == []
    assert report["metadata"]["period_start"] == "2025-01-01"
    assert report["metadata"]["issue_date"] == "2025-02-03"
    assert report["export"]["available_formats"] == ["csv", "xlsx"]
    lots = [o for o in report["observations"] if o["observation_type"] == "investment_lot"]
    assert len(lots) == 10  # Five monetary fields for each lot, with original dates and quantities.
    assert {o["dates"]["application"] for o in lots} == {"2024-10-15", "2024-11-01"}
    assert all(v["status"] == "passed" for v in report["validations"])


def test_btg_quantity_uses_printed_aggregate_precision_without_rounding_money():
    pages = [p.replace("40,00000", "40,00000004").replace("60,00000", "60,00000004") for p in fund_pages()]
    result = _parse_pdf_transactions_from_page_texts(pages)
    assert result.financial_document["events"] == []


@pytest.mark.parametrize("before,after", [
    ("66,00 1,00 - 65,00", "66,00 1,00 - 64,00"),
    ("100,00000 1,1000000", "101,00000 1,1000000"),
    ("31/01/25 -", "31/01/25 50,00"),
])
def test_btg_bad_net_quantity_or_uncovered_cash_activity_cannot_fall_back_to_bank_parser(before, after):
    with pytest.raises(PdfFinancialValidationError):
        _parse_pdf_transactions_from_page_texts([p.replace(before, after) for p in fund_pages()])
