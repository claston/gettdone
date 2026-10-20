import pytest

from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import _parse_pdf_transactions_from_page_texts


def summary_page():
    return "\n".join(
        [
            "Plataforma de Serviços Financeiros do Sicoob – SISBR",
            "Captação Remunerada",
            "Extrato Consolidado",
            "Data Emissão: 16/10/2026",
            "PERÍODO: JANEIRO/2025",
            "Saldo Bruto Anterior em 31/12/2024: 1.000,00",
            "Aplicações: 200,00",
            "Rendimentos Bruto: 30,00",
            "Resgates Líquidos: 100,00",
            "Imposto de Renda Retido: 5,00",
            "IOF Retido: 0,00",
            "Rendimento Líquido: 25,00",
            "Saldo Bruto em 31/01/2025: 1.125,00",
            "Imposto de Renda Previsto: 10,00",
            "IOF previsto: 1,00",
            "Saldo Liquido em 31/01/2025: 1.114,00",
            "REM116 Pág.1/ 1",
        ]
    )


def test_sicoob_period_summary_preserves_observations_without_inventing_dated_transactions():
    result = _parse_pdf_transactions_from_page_texts([summary_page()])
    doc = result.financial_document
    assert doc and doc["origin"] == "Sicoob"
    assert result.transactions == [] and doc["events"] == []
    assert len(doc["observations"]) == 11
    assert doc["metadata"]["period_start"] == "2025-01-01"
    assert doc["metadata"]["issue_date"] == "2026-10-16"
    assert doc["export"]["available_formats"] == ["csv", "xlsx"]
    assert all(v["status"] == "passed" for v in doc["validations"])


def test_sicoob_summary_with_bad_net_position_is_rejected():
    with pytest.raises(PdfFinancialValidationError):
        _parse_pdf_transactions_from_page_texts([summary_page().replace("1.114,00", "1.115,00")])
