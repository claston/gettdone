"""OFX Simples converts readable movements and reports incomplete-file evidence."""

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_recovery_case_fixes import mercado_pago_page

pytestmark = pytest.mark.pdf_acceptance

SANTANDER = """Santander
EXTRATO CONSOLIDADO INTELIGENTE
setembro/2026
Conta Corrente
Movimentação
Data Descrição No Documento Movimentos (R$) Saldo (R$)
Créditos Débitos
SALDO EM 31/08 100,00
01/09 PIX RECEBIDO - 50,00
02/09 PIX ENVIADO - 20,00-
SALDO EM 30/09 130,00
Saldos por Período
Página: 2/17"""


def assert_review(result, amounts):
    assert [row.amount for row in result.transactions] == amounts
    assert result.parse_metrics["partial_statement"] == 1
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "partial_statement" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("text,amounts", [
    (SANTANDER, [50, -20]),
    (mercado_pago_page().replace("1/1", "1/3"), [50, -20, 20]),
    ("Extrato de conta corrente BANCO TESTE\nData Histórico Valor Saldo\n"
     "01/09/2026 PIX RECEBIDO 50,00 C 150,00 C\n02/09/2026 PIX ENVIADO 20,00 D 130,00 C\nPágina: 3 de 8", [50, -20]),
])
def test_incomplete_page_numbers_are_review_warnings_across_layouts(text, amounts, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf((text.splitlines(),))
    assert_review(pdf_parser.parse_pdf_transactions(raw), amounts)


def test_readable_financial_mismatch_converts_with_warning_without_changing_amounts(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    text = mercado_pago_page().replace("Entradas: R$ 70,00", "Entradas: R$ 71,00")
    raw = generate_native_text_pdf((text.splitlines(),))
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [row.amount for row in result.transactions] == [50, -20, 20]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("page", ["Página: 2/17", "Página: 3 de 8", "Folha 2/9", "PAG 3", "extrato mensal jun 2026 003/008"])
def test_incomplete_pagination_is_a_non_blocking_signal(page):
    assert validate_statement_completeness(["Extrato de conta corrente\n" + page]) is True


@pytest.mark.parametrize("text", ["Extrato de conta corrente\n08/06\nPIX TESTE 10,00", "extrato mensal Itaú set 2026 001|001"])
def test_dates_and_complete_native_page_counters_do_not_generate_missing_page_warnings(text):
    assert validate_statement_completeness([text]) is False


def test_incomplete_file_without_readable_movements_still_reports_extraction_failure(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf((("Extrato de conta corrente", "Página: 2/17"),))
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(raw)


def test_financial_review_is_isolated_between_uploads(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    damaged = mercado_pago_page().replace("Entradas: R$ 70,00", "Entradas: R$ 71,00")
    reviewed = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((damaged.splitlines(),)))
    assert reviewed.parse_metrics["statement_validation_failed"] == 1
    complete = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((mercado_pago_page().splitlines(),)))
    assert complete.parse_metrics["financial_controls_validated"] == 1
    assert all("financial_validation_failed" not in row.warnings for row in complete.canonical_transactions)
