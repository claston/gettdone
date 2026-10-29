"""BB pending markers remain attached to their amount, independently of balance."""

from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedWord
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_oct28_new_statement_failures import bb_duplicate_opening
from tests.test_recovery_sweep_ocr import extraction


def bb_pending_marker(*, mode="detached", unknown=False, mismatch=False, misplaced=False, duplicate=False,
                      detached_balance_sign=False, unsigned_balance=False):
    source = bb_duplicate_opening(duplicate=False)
    page = source.pages[0]
    lines = [replace(line, text="350,00 C") if line.bbox["top"] == .4 and line.text == "150,00 C" else line
             for line in page.lines]
    balance = "151,00 C" if mismatch else "150,00 C"
    if detached_balance_sign or unsigned_balance:
        balance = balance.removesuffix(" C")
    pending = extraction([
        ("04/08/2026", .1, .34),
        ("144 Pix Enviado" if unknown else "511 Depósito bloquead.1d útil", .35, .34),
        ("1002", .65, .34),
        ("06/08/2026", .1, .37), ("631 Dep cheque caixa agencia", .35, .37),
        ("1003", .65, .37), ("200,00 C", .75, .37), ("350,00 C", .86, .37),
    ]).pages[0].lines
    cash = extraction([("200,00", .75, .34), ("*", .815, .355 if misplaced else .34),
                       (balance, .86, .34)]).pages[0].lines
    cash[0] = replace(cash[0], bbox={**cash[0].bbox, "width": .06})
    cash[1] = replace(cash[1], bbox={**cash[1].bbox, "width": .008})
    if detached_balance_sign:
        cash += extraction([("C", .94, .34)]).pages[0].lines
    if duplicate:
        cash.append(replace(cash[1], id="duplicate-marker", bbox={**cash[1].bbox, "left": .82}))
    words = []
    if mode in {"merged_text", "merged_words"}:
        cash = [replace(cash[0], text="200,00 * " + balance, bbox={**cash[0].bbox, "width": .19})]
        if mode == "merged_words":
            for index, (text, x, width) in enumerate([
                ("200,00", .75, .075), ("*", .83, .006), (balance.split()[0], .85, .04), ("C", .90, .01),
            ]):
                words.append(ExtractedWord(f"pending-word-{index}", 1, index + 1, text, 99,
                    {"left": x, "top": .34, "width": width, "height": .01}, "PRINTED"))
            cash[0] = replace(cash[0], word_ids=tuple(word.id for word in words))
    additions = [replace(line, id=f"pending-{i}", line_index=100+i) for i, line in enumerate([*pending, *cash])]
    return replace(source, pages=[replace(page, lines=[*lines, *additions], words=[*page.words, *words])])


def bb_pending_native():
    source = bb_pending_marker()
    return grid_pdf([[(line.text, .785 if line.text == "200,00" else line.bbox["left"], line.bbox["top"])
                     for line in source.pages[0].lines]])


@pytest.mark.parametrize("mode", ["detached", "merged_text", "merged_words", "native"])
def test_pending_amount_and_printed_balance_keep_the_deposit_out_of_available_cash(mode):
    result = (pdf_parser.parse_pdf_transactions(bb_pending_native()) if mode == "native"
              else pdf_parser._parse_scanned_pdf_with_textract_text_pages(bb_pending_marker(mode=mode)))
    assert [t.amount for t in result.transactions] == [50, 200]
    assert [t.date for t in result.transactions] == ["2026-08-03", "2026-08-06"]
    assert result.parse_metrics["non_cash_entries_excluded"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 350
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert "pending_deposits_excluded" in result.canonical_transactions[0].warnings
    assert all(row.source_regions for row in result.canonical_transactions)


@pytest.mark.parametrize("kwargs", [
    {"unknown": True}, {"misplaced": True}, {"duplicate": True},
    {"unsigned_balance": True},
])
def test_marker_needs_an_unambiguous_amount_and_a_blocked_deposit_description(kwargs):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(bb_pending_marker(**kwargs))


def test_pending_row_balance_divergence_requires_review_without_changing_movements():
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(bb_pending_marker(mismatch=True))
    assert [t.amount for t in result.transactions] == [50, 200]
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


def test_balance_cd_can_be_detached_without_becoming_the_pending_deposit_sign():
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(bb_pending_marker(detached_balance_sign=True))
    assert [t.amount for t in result.transactions] == [50, 200]
    assert result.parse_metrics["non_cash_entries_excluded"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
