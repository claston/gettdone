"""Warnings follow the OCR evidence, independently of the extraction provider."""

from dataclasses import replace

import pytest

from app.application import ocr_cash_statement
from app.application.pdf_layout_inference import PdfLayoutInference
from tests.test_ocr_structure_experiment import evidence


@pytest.fixture(autouse=True)
def layout(monkeypatch):
    monkeypatch.setattr(
        ocr_cash_statement, "infer_pdf_layout", lambda text: PdfLayoutInference("asaas_extrato_conta_digital_movimentacoes_v1", 0.99, False)
    )


@pytest.mark.parametrize("tables", [False, True])
@pytest.mark.parametrize("column,code", [(0, "ocr_date_confidence_unconfirmed"), (2, "ocr_amount_confidence_unconfirmed")])
def test_low_confidence_marks_only_the_affected_row_and_field(tables, column, code):
    source = evidence(tables=tables)
    page = source.pages[0]
    if tables:
        table = page.tables[0]
        rows = [list(row) for row in table.rows]
        rows[1][column] = replace(rows[1][column], confidence=40)
        page = replace(page, tables=[replace(table, rows=rows)])
    else:
        token = "01/01/2026" if column == 0 else "20,00"
        page = replace(page, words=[replace(word, confidence=40) if word.text == token else word for word in page.words])
    result = ocr_cash_statement.project_shared_cash_extraction(replace(source, pages=[page]))
    assert code in result.canonical_transactions[0].warnings
    assert all(not row.warnings for row in result.canonical_transactions[1:])
    assert all("manual_review_recommended" not in row.warnings for row in result.canonical_transactions)
    assert result.parse_metrics["ocr_balance_arithmetic_matches"] == 1
    assert result.parse_metrics["ocr_review_fields_count"] == 1
    assert result.parse_metrics["ocr_review_rows_count"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert [row.amount for row in result.canonical_transactions] == [20, -5, -5]
    assert all(row.source_page == 1 and row.source_regions for row in result.canonical_transactions)


def test_uncertain_control_is_a_document_warning_even_when_the_arithmetic_matches():
    source = evidence()
    page = source.pages[0]
    page = replace(page, words=[replace(word, confidence=30) if word.text == "110,00" else word for word in page.words])
    result = ocr_cash_statement.project_shared_cash_extraction(replace(source, pages=[page]))
    assert result.canonical_transactions[0].warnings == ["ocr_control_confidence_unconfirmed"]
    assert all(not row.warnings for row in result.canonical_transactions[1:])
    assert result.parse_metrics["ocr_balance_arithmetic_matches"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["ocr_review_rows_count"] == 0


def test_unreadable_row_still_requires_coverage_and_financial_review():
    source = evidence(tables=True)
    page = source.pages[0]
    table = page.tables[0]
    rows = [list(row) for row in table.rows]
    rows[1][2] = replace(rows[1][2], text="2O,00")
    result = ocr_cash_statement.project_shared_cash_extraction(replace(source, pages=[replace(page, tables=[replace(table, rows=rows)])]))
    assert len(result.canonical_transactions) == 2
    assert {"partial_statement", "financial_validation_failed"}.issubset(result.canonical_transactions[0].warnings)
    assert result.parse_metrics["ocr_balance_arithmetic_matches"] == 0
    assert result.parse_metrics["ocr_unresolved_rows"] == 1


def test_headerless_readable_rows_have_a_document_warning_without_false_partial_extraction():
    result = ocr_cash_statement.project_shared_cash_extraction(evidence(header=False))
    assert len(result.canonical_transactions) == 3
    assert all("partial_statement" not in row.warnings for row in result.canonical_transactions)
