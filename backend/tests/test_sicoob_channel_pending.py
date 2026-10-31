"""Explicit blocked cheque contexts keep pending entries out of available cash."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_recovery_sweep_ocr import extraction
from tests.test_sicoob_statement_context import modern_cells, sisbr_pending_cells


def modern_pending_cells(*, history="DEP CH.CANAL ATEND.1D", token="R$ 200,00*", detached=False, mismatch=False):
    cells = [cell for cell in modern_cells(expiry=False) if cell[2] <= .21]
    ledger = [
        ("30/06", "SALDO ANTERIOR", "R$ 100,00C"),
        ("01/07", "PIX RECEBIDO", "R$ 50,00C"),
        ("01/07", "SALDO DO DIA", "R$ 150,00C"),
        ("02/07", history, token),
        ("02/07", "SALDO DO DIA", "R$ 151,00C" if mismatch else "R$ 150,00C"),
        ("03/07", "LIBERAÇÃO DE DEPÓSITO BLOQUEADO", "R$ 200,00C"),
        ("03/07", "PIX EMITIDO OUTRA IF", "R$ 20,00D"),
        ("03/07", "PIX EMITIDO OUTRA IF", "R$ 20,00D"),
        ("03/07", "SALDO DO DIA", "R$ 310,00C"),
    ]
    for index, (date, label, value) in enumerate(ledger):
        y = .27+index*.04
        cells += [(date, .05, y), (label, .24, y), (value.rstrip("*") if detached and index == 3 else value, .86, y)]
        if detached and index == 3:
            cells.append(("*", .97, y+.004))
    return cells + [("RESUMO", .24, .70), ("Saldo em conta:", .24, .74), ("R$ 310,00C", .86, .74)]


def source_pages(pages):
    source = extraction(pages[0])
    result = []
    for index, cells in enumerate(pages, 1):
        page = extraction(cells, page=index).pages[0]
        result.append(replace(page, lines=[replace(line, id=f"p{index}-{line.id}") for line in page.lines]))
    return replace(source, pages=result)


def parse_pages(pages, representation):
    if representation == "native":
        return pdf_parser.parse_pdf_transactions(grid_pdf(pages))
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(source_pages(pages))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("history", ["DEP CH.CANAL ATEND.1D", "DEP CH.CANAL ATEND.1 D", "DP CH.CANAL ATEN 1D", "DEP CH.CANAL ATEND."])
@pytest.mark.parametrize("detached", [False, True])
def test_modern_pending_channel_deposit_is_not_cash_but_its_release_is(history, detached, representation):
    result = parse_pages([modern_pending_cells(history=history, detached=detached)], representation)
    assert [row.amount for row in result.transactions] == [50, 200, -20, -20]
    assert [row.date for row in result.transactions] == ["2026-07-01", "2026-07-03", "2026-07-03", "2026-07-03"]
    assert result.parse_metrics["non_cash_entries_excluded"] == 1
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 310
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert "pending_deposits_excluded" in result.parse_metrics["canonical_warning_types"]
    assert all(row.source_regions and row.source_line_refs for row in result.canonical_transactions)


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("history", ["DP CH.CANAL ATEN 1D", "DEP CH.CANAL ATEND.1D", "DEP.CH.BLOQ.1D-INTE"])
def test_sisbr_uses_same_pending_context_and_preserves_release_and_cross_page_document(history, representation):
    first, second = sisbr_pending_cells(history=history)
    # Reference belongs to the pending row but starts on the next page.
    first = [cell for cell in first if cell[0] != "DOC.: 1002"]
    second.insert(0, ("DOC.: 1002", .26, .02))
    result = parse_pages([first, second], representation)
    assert [row.amount for row in result.transactions] == [50, 200, -20]
    assert result.parse_metrics["non_cash_entries_excluded"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 330
    assert "pending_deposits_excluded" in result.parse_metrics["canonical_warning_types"]


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("options", [
    {"history": "PIX RECEBIDO"},
    {"history": "PIX RECEBIDO DEP CH.CANAL ATEND.1D"},
    {"history": "LIBERAÇÃO DE DEPÓSITO BLOQUEADO"},
    {"token": "R$ 200,00"},
    {"token": "R$ ?00,00*"},
    {"token": "R$ 200,00C*"},
    {"token": "R$ 200,00**"},
])
def test_pending_channel_context_never_invents_marker_amount_or_cash_sign(representation, options):
    with pytest.raises(PdfFinancialValidationError):
        parse_pages([modern_pending_cells(**options)], representation)


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_pending_only_day_balance_difference_keeps_cash_and_requires_review(representation):
    result = parse_pages([modern_pending_cells(mismatch=True)], representation)
    assert [row.amount for row in result.transactions] == [50, 200, -20, -20]
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "financial_validation_failed" in result.parse_metrics["canonical_warning_types"]


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_explicit_credit_on_same_channel_history_is_preserved_as_cash(representation):
    result = parse_pages([modern_pending_cells(token="R$ 200,00C")], representation)
    assert [row.amount for row in result.transactions] == [50, 200, 200, -20, -20]
    assert result.parse_metrics["non_cash_entries_excluded"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_extra_detached_cash_sign_does_not_compete_with_pending_marker(representation):
    cells = modern_pending_cells(detached=True) + [("C", .95, .27+3*.04)]
    with pytest.raises(PdfFinancialValidationError):
        parse_pages([cells], representation)
