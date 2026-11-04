"""Investment exports preserve positions and dated redemptions without cash projection."""

import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError


def custody():
    return ["""Extrato de Custódia Mensal/Período
Período de: 07/2026
Posição em Custódia
Ativo Especif. Cód. Neg Saldo Cotação Valor Tipo
FUNDO TESTE CI TEST11 100 9,86 986,00 Cota
Proventos em Dinheiro - Provisionados
Não foram encontrados resultados para esta pesquisa.
Proventos em Dinheiro - Creditados
Ativo Especif. Cód. Neg Crédito no Mês Saldo
FUNDO TESTE CI TEST11 JUL/2026 10,00
Ágora Corretora de Títulos e Valores Mobiliários S/A""",
"Movimentação de Ativos\nNão foram encontrados resultados para esta pesquisa.\nÁgora Corretora"]


def fcbr76():
    headings = ("VALOR APLICADO", "SALDO EM", "SALDO EM", "REND. BRUTO", "REND. BRUTO", "IR", "RESGATE BRUTO")
    def fields(values):
        return "".join(f"{value:>14}" for value in values)
    header = f"{'NUMERO':>6} {'APLICACAO':>10} {'VENCIMENTO':>10}"+fields(headings)
    return "\n".join([
        "BRB - BANCO DE BRASILIA S.A.", "FCBR76 - EXTRATO DE APLICACOES PARA SIMPLES CONFERENCIA",
        "APLICACOES EM CDB/RDB - CONTA CORRENTE", "PERIODO: 31/08/2026 A 30/09/2026", header,
        f"{'1':>6} 01/01/2026 01/01/2027"+fields(["100,00", "100,00", "81,00", "0,80", "0,80", "", ""]),
        "100,00% CDI".ljust(28)+fields(["80,20", "", "", "", "", "", ""]),
        "        RC      1 02/09/2026".ljust(28)+fields(["20,00", "", "", "0,20", "0,20", "0,10", "20,20"]),
        " "*28+fields(["", "", "", "", "", "0,10", "20,00"]),
        "TOTAL".ljust(28)+fields(["", "100,00", "81,00", "1,00", "1,00", "0,10", "20,20"]),
        " "*28+fields(["", "", "", "", "", "0,10", "20,00"]),
    ])


def test_custody_monthly_credit_does_not_invent_a_day_or_cash_movement():
    result = pdf_parser._parse_pdf_transactions_from_page_texts(custody())
    doc = result.financial_document
    assert not result.transactions and not doc["events"]
    assert len(doc["observations"]) == 2
    assert doc["observations"][0]["amounts"] == {"gross": "986.00"}
    income = doc["observations"][1]
    assert income["attributes"]["credit_month"] == "JUL/2026" and income["dates"] == {}
    assert all(v["status"] == "passed" for v in doc["validations"])
    assert doc["export"]["available_formats"] == ["csv", "xlsx"]


def test_unreadable_asset_row_or_missing_later_section_is_not_silently_dropped():
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_pdf_transactions_from_page_texts([custody()[0].replace("986,00", "98?,00"), custody()[1]])
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_pdf_transactions_from_page_texts(custody()[:1])


def test_fcbr76_keeps_position_redemption_tax_and_unlabelled_complement():
    result = pdf_parser._parse_pdf_transactions_from_page_texts([fcbr76()], preserve_layout_spacing=True)
    doc = result.financial_document
    assert not result.transactions and not doc["events"]
    assert len(doc["observations"]) == 3
    assert doc["observations"][0]["attributes"]["printed_principal_complement"] == "80.20"
    redemption = doc["observations"][1]
    assert redemption["dates"] == {"redemption": "2026-09-02"}
    assert redemption["amounts"]["net_redemptions"] == "20.00"
    assert redemption["amounts"]["iof"] == "0.10"
    assert all(v["status"] == "passed" for v in doc["validations"])


def test_fcbr76_does_not_omit_a_redemption_without_its_net_value():
    report = fcbr76().splitlines()
    del report[8]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_pdf_transactions_from_page_texts(["\n".join(report)], preserve_layout_spacing=True)


def test_investment_reports_do_not_mix_different_owners():
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_pdf_transactions_from_page_texts([fcbr76()+"\nC/C: 12345-6\nC/C: 54321-0"])
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_pdf_transactions_from_page_texts([custody()[0]+"\nCliente: 123\nCliente: 456", custody()[1]])


def test_fcbr76_exports_original_fields_and_redemption_values(tmp_path):
    from openpyxl import load_workbook

    from app.application.financial_document_export import write_financial_artifacts

    result = pdf_parser._parse_pdf_transactions_from_page_texts([fcbr76()], preserve_layout_spacing=True)
    write_financial_artifacts(tmp_path, result.financial_document)
    text = (tmp_path / "converted.csv").read_text(encoding="utf-8-sig")
    assert "2026-09-02" in text and "80.20" in text and "100,00% CDI" in text
    book = load_workbook(tmp_path / "converted.xlsx")
    assert book["Posições"].max_row == 4
    assert not (tmp_path / "converted.ofx").exists()
