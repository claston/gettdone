from dataclasses import replace

import pytest

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord, RawDocumentExtraction
from app.application.pdf_parser import _parse_scanned_pdf_with_textract_text_pages


def _parse(pages):
    return _parse_scanned_pdf_with_textract_text_pages(RawDocumentExtraction("aws_textract", "synthetic", pages, {}))


def _line(identifier, text, left, top):
    return ExtractedLine(identifier, 1, int(top * 10000), text,
                         bbox={"left": left, "top": top, "width": 0.06, "height": 0.008})


def _word(identifier, text, left, top):
    return ExtractedWord(identifier, 1, int(top * 10000), text, confidence=60.1, text_type="PRINTED",
                         bbox={"left": left, "top": top, "width": 0.06, "height": 0.008})


def _page(debit="20,000,00-"):
    page = ExtractedPage(1, lines=[
        _line("title", "ItaúEmpresas Extrato mensal Conta Corrente junho 2026 Movimentação créditos débitos", 0.1, 0.03),
        _line("date-header", "data", 0.25, 0.1),
        _line("description-header", "descrição", 0.35, 0.1),
        _line("credit-header", "entradas R$", 0.59, 0.1),
        _line("debit-header", "saídas R$", 0.71, 0.1),
        _line("balance-header", "saldo R$", 0.86, 0.1),
        _line("opening", "Saldo anterior", 0.35, 0.13),
    ], words=[_word("opening-balance", "274.704,63", 0.86, 0.13)])
    movements = [
        ("08/06", "Apl Aplic Aut Mais", "2.200,00-", 0.72, "1,00"),
        ("09/06", "PIX ENVIADO EXEMPLO", debit, 0.72, None),
        (None, "PIX RECEBIDO CLIENTE A", "5.697,36", 0.61, None),
        (None, "PIX RECEBIDO CLIENTE B", "12.000,00", 0.61, None),
        (None, "PIX RECEBIDO CLIENTE C", "15.000,00", 0.61, None),
        (None, "Apl Aplic Aut Mais", "12.697,36-", 0.72, "1,00"),
    ]
    for index, (date, description, amount, left, balance) in enumerate(movements):
        top = 0.2 + index * 0.03
        if date:
            page.lines.append(_line(f"date-{index}", date, 0.25, top))
        page.lines.append(_line(f"description-{index}", description, 0.35, top))
        page.lines.append(_line(f"amount-line-{index}", amount, left, top))
        page.words.append(_word(f"amount-{index}", amount, left, top))
        if balance:
            page.words.append(_word(f"balance-{index}", balance, 0.86, top))
    page.lines.extend([
        _line("investment", "Saldo Aplic Aut Mais", 0.35, 0.4),
        _line("end", "Saldo em C/C", 0.35, 0.45),
    ])
    page.words.append(_word("investment-balance", "287.401,99", 0.86, 0.4))
    return page


def _change_word(page, identifier, **changes):
    page.words[:] = [replace(word, **changes) if word.id == identifier else word for word in page.words]


@pytest.mark.parametrize("token", ["20,000,00-", "20,000,00", "-20,000,00"])
def test_recovers_printed_debit_only_when_cash_balances_confirm_it(token):
    page = _page(token)
    result = _parse([page])
    assert [row.amount for row in result.canonical_transactions] == [-2200, -20000, 5697.36, 12000, 15000, -12697.36]
    assert result.parse_metrics["ocr_unresolved_rows"] == 0
    assert result.parse_metrics["opening_balance_ambiguous"] == 1
    recovered = result.canonical_transactions[1]
    assert recovered.date == "2026-06-09"
    assert next(word.text for word in result.source_extraction.pages[0].words if word.id == "amount-1") == token
    assert recovered.source_page == 1
    assert any(region.left == 0.72 and region.top == pytest.approx(0.23) for region in recovered.source_regions)
    assert next(word.text for word in page.words if word.id == "amount-1") == token


def test_credit_column_keeps_sign_despite_a_trailing_pen_dash():
    page = _page("20.000,00-")
    _change_word(page, "amount-3", text="12,000,00-")
    result = _parse([page])
    assert len(result.canonical_transactions) == 6
    assert result.canonical_transactions[3].amount == 12000
    assert result.parse_metrics["ocr_unresolved_rows"] == 0


@pytest.mark.parametrize("application, confirmed", [("22.697,36-", True), ("12.697,36-", False)])
def test_ten_thousand_repair_is_generic_but_still_needs_matching_balances(application, confirmed):
    page = _page("10,000,00")
    _change_word(page, "amount-5", text=application)
    result = _parse([page])
    assert any(row.amount == -10000 for row in result.canonical_transactions) is confirmed
    assert len(result.canonical_transactions) == (6 if confirmed else 5)
    assert result.parse_metrics["ocr_unresolved_rows"] == (0 if confirmed else 1)


def test_recovers_multiple_thousands_groups_without_changing_digits():
    page = _page()
    _change_word(page, "amount-1", text="1,020,000,00-")
    _change_word(page, "balance-0", text="1.000.001,00")
    result = _parse([page])
    assert result.canonical_transactions[1].amount == -1020000
    assert result.parse_metrics["ocr_unresolved_rows"] == 0


@pytest.mark.parametrize("token", ["20,00,00-", "20,000,0-", "20,000.00-", "20,000,000-", "20,000,00--", "-20,000,00-",
                                  "NOTA20,000,00-", "20,000,00- NOTA"])
def test_does_not_guess_ambiguous_or_malformed_amounts(token):
    result = _parse([_page(token)])
    assert len(result.canonical_transactions) == 5
    assert result.parse_metrics["ocr_unresolved_rows"] >= 1


@pytest.mark.parametrize("text_type", ["HANDWRITING", None])
def test_does_not_normalize_values_without_printed_evidence(text_type):
    page = _page()
    _change_word(page, "amount-1", text_type=text_type)
    result = _parse([page])
    assert len(result.canonical_transactions) == 5
    assert result.parse_metrics["ocr_unresolved_rows"] >= 1


@pytest.mark.parametrize("balance_id", ["balance-0", "balance-5"])
def test_needs_cash_balances_on_both_sides_not_the_consolidated_opening_or_investment(balance_id):
    page = _page()
    page.words[:] = [word for word in page.words if word.id != balance_id]
    result = _parse([page])
    assert len(result.canonical_transactions) == 5
    assert result.parse_metrics["ocr_unresolved_rows"] == 1


def test_rejects_normalization_when_the_cash_ledger_does_not_close():
    page = _page()
    _change_word(page, "balance-5", text="2,00")
    result = _parse([page])
    assert len(result.canonical_transactions) == 5
    assert result.parse_metrics["ocr_unresolved_rows"] == 1


def test_handwritten_balance_cannot_confirm_a_normalized_amount():
    page = _page()
    _change_word(page, "balance-5", text_type="HANDWRITING")
    result = _parse([page])
    assert len(result.canonical_transactions) == 5
    assert result.parse_metrics["ocr_unresolved_rows"] == 1


def test_does_not_resolve_two_amounts_on_the_same_row_by_picking_what_balances():
    page = _page()
    page.words.append(_word("extra-value", "19,000,00-", 0.73, 0.23))
    result = _parse([page])
    assert len(result.canonical_transactions) == 5
    assert result.parse_metrics["ocr_unresolved_rows"] >= 1


def test_validates_recovered_amount_across_page_boundaries():
    first = _page()
    second = replace(_page(), page_number=2)
    first.lines[:] = [line for line in first.lines if line.bbox["top"] <= 0.2]
    first.words[:] = [word for word in first.words if word.bbox["top"] <= 0.2]
    second.lines[:] = [replace(line, page_number=2) for line in second.lines
                       if line.bbox["top"] < 0.13 or line.bbox["top"] > 0.2]
    second.words[:] = [replace(word, page_number=2) for word in second.words if word.bbox["top"] > 0.2]
    result = _parse([first, second])
    assert len(result.canonical_transactions) == 6
    assert result.canonical_transactions[1].source_page == 2
    assert result.canonical_transactions[1].amount == -20000
    assert result.parse_metrics["ocr_unresolved_rows"] == 0


def test_complete_ocr_parse_recovers_transaction_and_clears_balance_warning():
    result = _parse_scanned_pdf_with_textract_text_pages(RawDocumentExtraction("aws_textract", "synthetic", [_page()], {}))
    assert result.parse_metrics["selected_parser"] == "layout_specific_itau_monthly_ocr"
    assert len(result.transactions) == 6
    assert result.transactions[1].amount == -20000
    assert result.parse_metrics["ocr_unresolved_rows"] == 0
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert not any(row.warnings for row in result.canonical_transactions)
    assert result.parse_metrics["opening_balance_ambiguous"] == 1


def test_standard_amount_still_works_without_the_additional_recovery_controls():
    page = _page("20.000,00-")
    page.words[:] = [word for word in page.words if word.id != "balance-0"]
    result = _parse([page])
    assert len(result.canonical_transactions) == 6
    assert result.canonical_transactions[1].amount == -20000
    assert result.parse_metrics["ocr_unresolved_rows"] == 0
