"""Daily closing headers precede explicit signed protocol movements."""
import pytest

from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import _parse_scanned_pdf_with_textract_text_pages, parse_pdf_transactions
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_oct20_followup_ocr import ocr


def protocol_pages():
    header = [("Extrato financeiro", .06, .03), ("Data", .06, .10), ("Descrição", .20, .10),
              ("Protocolo", .66, .10), ("Valor (R$)", .86, .10)]
    def row(date, description, value, y, reference=None):
        return [(date, .06, y), (description, .20, y), (value, .86, y)] + (
            [(reference, .63, y)] if reference else [])
    return [header + row("01/04/2026", "Saldo do dia", "130,00", .20)
            + row("01/04/2026", "PIX RECEBIDO CLIENTE", "+50,00", .28, "1000000001")
            + row("01/04/2026", "PIX ENVIADO", "-20,00", .36, "1000000002")
            + row("02/04/2026", "Saldo do dia", "135,00", .90),
            header + row("02/04/2026", "PIX RECEBIDO CLIENTE", "+10,00", .20, "1000000003")
            + row("02/04/2026", "TARIFA", "-5,00", .28, "1000000004"),
            header + row("04/05/2026", "Saldo do dia", "145,00", .20)
            + row("04/05/2026", "PIX RECEBIDO CLIENTE", "+10,00", .28, "1000000005")]


@pytest.mark.parametrize("native", [False, True], ids=["ocr", "native"])
def test_all_pages_dates_protocols_and_preceding_daily_controls_are_preserved(monkeypatch, native):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    pages = protocol_pages()
    result = parse_pdf_transactions(grid_pdf(pages)) if native else _parse_scanned_pdf_with_textract_text_pages(ocr(pages))
    assert [r.amount for r in result.transactions] == [50, -20, 10, -5, 10]
    assert [r.date for r in result.transactions] == ["2026-04-01"] * 2 + ["2026-04-02"] * 2 + ["2026-05-04"]
    assert [r.source_page for r in result.canonical_transactions] == [1, 1, 2, 2, 3]
    assert [r.external_reference_id for r in result.canonical_transactions] == [f"100000000{i}" for i in range(1, 6)]
    assert all(r.source_regions and r.source_line_refs for r in result.canonical_transactions)
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["derived_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 145
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("damage", ["amount", "sign", "date", "reference"])
def test_unreadable_later_movement_never_becomes_a_successful_partial_result(damage):
    pages = protocol_pages()
    before, after = {"amount": ("+10,00", "+XX,00"), "sign": ("+10,00", "10,00"),
                     "date": ("04/05/2026", "04/05/20??"), "reference": ("1000000005", "??")}[damage]
    pages[-1] = [(after if text == before else text, x, y) for text, x, y in pages[-1]]
    with pytest.raises(PdfFinancialValidationError):
        _parse_scanned_pdf_with_textract_text_pages(ocr(pages))


def test_printed_balance_mismatch_remains_visible_without_changing_movements():
    pages = protocol_pages()
    pages[-1] = [("146,00" if text == "145,00" else text, x, y) for text, x, y in pages[-1]]
    result = _parse_scanned_pdf_with_textract_text_pages(ocr(pages))
    assert [r.amount for r in result.transactions] == [50, -20, 10, -5, 10]
    assert result.parse_metrics["verified_closing_balance"] == 146
    assert "financial_validation_failed" in result.parse_metrics["canonical_warning_types"]


@pytest.mark.parametrize("native", [False, True], ids=["ocr", "native"])
def test_legitimate_repeated_movements_and_daily_headers_are_not_deduplicated(monkeypatch, native):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    pages = protocol_pages()
    pages[0].extend((text, x, .32) for text, x, y in list(pages[0]) if y == .28)
    changes = {"130,00": "180,00", "135,00": "185,00", "145,00": "195,00"}
    pages = [[(changes.get(text, text), x, y) for text, x, y in page] for page in pages]
    pages[1].extend([("02/04/2026", .06, .14), ("Saldo do dia", .20, .14), ("185,00", .86, .14)])
    result = parse_pdf_transactions(grid_pdf(pages)) if native else _parse_scanned_pdf_with_textract_text_pages(ocr(pages))
    assert [r.amount for r in result.transactions] == [50, 50, -20, 10, -5, 10]
    assert [r.external_reference_id for r in result.canonical_transactions][:2] == ["1000000001"] * 2
    assert result.canonical_transactions[0].source_line_refs != result.canonical_transactions[1].source_line_refs
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["derived_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 195
