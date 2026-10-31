"""Provider-independent structure and financial interpretation, not production routing."""

from dataclasses import replace
from decimal import Decimal

import pytest

from app.application.document_extraction_models import (
    ExtractedCell,
    ExtractedLine,
    ExtractedPage,
    ExtractedTable,
    ExtractedWord,
    RawDocumentExtraction,
)
from app.application.ocr_financial_interpretation import CashTablePolicy, interpret_cash_table
from app.application.ocr_structure import OcrColumn, OcrTableSchema, reconstruct_ocr_table

SCHEMA = OcrTableSchema(
    columns=(
        OcrColumn("date", ("Data",), r"\d{2}/\d{2}/\d{4}"),
        OcrColumn("description", ("Movimentações", "Histórico")),
        OcrColumn("amount", ("Valor",), r"[+-]?\d+,\d{2}"),
    )
)
POLICY = CashTablePolicy(opening_labels=("Saldo inicial",), closing_labels=("Saldo final",))


def evidence(*, tables=False, tilt=-0.025, page_number=1, provider="arbitrary_reader", header=True):
    words, lines = [], []

    def add(text, x, y, width=0.09):
        identifier = f"p{page_number}:w{len(words)}"
        box = {"left": x, "top": y + tilt * x, "width": width, "height": 0.01}
        word = ExtractedWord(
            identifier,
            page_number,
            len(words) + 1,
            text,
            99,
            box,
            polygon=[
                {"x": x, "y": box["top"]},
                {"x": x + width, "y": y + tilt * (x + width)},
                {"x": x + width, "y": y + tilt * (x + width) + 0.01},
                {"x": x, "y": box["top"] + 0.01},
            ],
        )
        words.append(word)
        lines.append(ExtractedLine(identifier + ":line", page_number, len(lines) + 1, text, 99, box, (identifier,)))
        return word

    add("Saldo inicial", 0.2, 0.10, 0.20)
    add("100,00", 0.83, 0.10)
    add("Saldo final", 0.2, 0.15, 0.18)
    add("110,00", 0.83, 0.15)
    if header:
        add("Data", 0.05, 0.25, 0.04)
        add("Histórico", 0.23, 0.25, 0.12)
        add("Valor", 0.86, 0.25, 0.05)
    entries = [("01/01/2026", "Crédito", "20,00"), ("02/01/2026", "Tarifa", "-5,00"), ("02/01/2026", "Tarifa", "-5,00")]
    table_rows = [[ExtractedCell(1, i + 1, text) for i, text in enumerate(("Data", "Histórico", "Valor"))]]
    for index, (date, description, value) in enumerate(entries, 2):
        y = 0.3 + (index - 2) * 0.05
        row_words = [add(date, 0.05, y), add(description, 0.23, y, 0.15), add(value, 0.83, y)]
        table_rows.append([ExtractedCell(index, i + 1, w.text, 99, w.bbox, w.id + ":cell", (w.id,)) for i, w in enumerate(row_words)])
    table = [ExtractedTable("table", page_number, 1, table_rows)] if tables else []
    page = ExtractedPage(page_number, lines=lines, words=words, tables=table)
    return RawDocumentExtraction(provider, "synthetic", [page], {})


@pytest.mark.parametrize("tables,provider", [(False, "text_reader"), (True, "analysis_reader"), (False, "different_vendor")])
def test_one_financial_policy_consumes_all_reader_representations(tables, provider):
    source = evidence(tables=tables, provider=provider)
    structure = reconstruct_ocr_table(source, SCHEMA)
    result = interpret_cash_table(source, structure, POLICY)
    assert [(row.date, row.amount) for row in result.movements] == [
        ("2026-01-01", Decimal("20.00")),
        ("2026-01-02", Decimal("-5.00")),
        ("2026-01-02", Decimal("-5.00")),
    ]
    assert result.opening == Decimal("100.00") and result.closing == Decimal("110.00")
    assert result.balance_matches and not result.requires_review
    assert all(row.source_page == 1 and row.source_word_ids and row.source_regions for row in result.movements)


def test_word_reconstruction_handles_skew_and_unreliable_provider_reading_order():
    source = evidence(tilt=0.09)
    page = source.pages[0]
    source = replace(source, pages=[replace(page, words=list(reversed(page.words)), lines=list(reversed(page.lines)))])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert [r.amount for r in result.movements] == [Decimal("20.00"), Decimal("-5.00"), Decimal("-5.00")]
    assert not result.requires_review


def test_table_and_word_views_do_not_double_count_the_same_physical_movements():
    source = evidence(tables=True)
    structure = reconstruct_ocr_table(source, SCHEMA)
    assert len(structure.rows) == 3
    assert structure.expected_rows == 3
    assert [row.cells["description"].text for row in structure.rows] == ["Crédito", "Tarifa", "Tarifa"]


def test_missing_provider_table_row_is_explicitly_reported_by_independent_word_coverage():
    source = evidence(tables=True)
    page = source.pages[0]
    source = replace(source, pages=[replace(page, tables=[replace(page.tables[0], rows=page.tables[0].rows[:-1])])])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert result.requires_review
    assert any(issue.code == "row_coverage_mismatch" for issue in result.issues)


def test_wrong_closing_does_not_rewrite_or_drop_movements():
    source = evidence()
    page = source.pages[0]
    source = replace(
        source,
        pages=[
            replace(
                page,
                words=[replace(w, text="999,00") if w.text == "110,00" else w for w in page.words],
                lines=[replace(line, text="999,00") if line.text == "110,00" else line for line in page.lines],
            )
        ],
    )
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert [r.amount for r in result.movements] == [Decimal("20.00"), Decimal("-5.00"), Decimal("-5.00")]
    assert result.closing == Decimal("999.00") and not result.balance_matches and result.requires_review


def test_unreadable_amount_is_retained_as_an_unresolved_row_and_cannot_validate():
    source = evidence(tables=True)
    page = source.pages[0]
    table = page.tables[0]
    bad = [*table.rows[1][:-1], replace(table.rows[1][-1], text="2O,00")]
    source = replace(source, pages=[replace(page, tables=[replace(table, rows=[table.rows[0], bad, *table.rows[2:]])])])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert result.requires_review and not result.balance_matches
    assert len(result.movements) == 2 and any(i.code == "unreadable_amount" for i in result.issues)


def test_geometry_free_text_remains_explicitly_unresolved():
    source = evidence()
    page = source.pages[0]
    source = replace(source, pages=[replace(page, words=[], lines=[replace(line, bbox=None) for line in page.lines])])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert result.requires_review
    assert any(i.code == "geometry_unavailable" for i in result.issues)


def test_missing_header_on_a_continuation_does_not_discard_readable_rows():
    first = evidence()
    continuation = evidence(header=False, page_number=2)
    page = continuation.pages[0]
    # Opening/closing controls belong to the document, not to this continuation.
    page = replace(
        page, words=[w for w in page.words if w.bbox["top"] > 0.2], lines=[line for line in page.lines if line.bbox["top"] > 0.2]
    )
    source = replace(first, pages=[first.pages[0], page])
    structure = reconstruct_ocr_table(source, SCHEMA)
    assert len(structure.rows) == 6 and structure.expected_rows == 6
    assert {row.page_number for row in structure.rows} == {1, 2}


def test_unreadable_later_page_prevents_a_falsely_complete_result():
    source = evidence()
    source = replace(source, pages=[*source.pages, ExtractedPage(2, lines=[ExtractedLine("bad", 2, 1, "unreadable continuation")])])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert len(result.movements) == 3 and result.requires_review
    assert any(i.page_number == 2 for i in result.issues)


def test_multiple_opening_controls_require_block_segmentation_instead_of_a_global_balance():
    source = evidence()
    other = evidence(page_number=2)
    source = replace(source, pages=[source.pages[0], other.pages[0]])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert len(result.movements) == 6 and not result.balance_matches and result.requires_review
    assert any(i.code == "multiple_financial_blocks" for i in result.issues)


def test_structure_supports_other_column_roles_without_bank_or_provider_branches():
    source = evidence(tables=True)
    page = source.pages[0]
    table = page.tables[0]
    renamed = [replace(row[1], text="Referência") if i == 0 else row[1] for i, row in enumerate(table.rows)]
    new_rows = [[row[0], renamed[i], row[2]] for i, row in enumerate(table.rows)]
    source = replace(source, pages=[replace(page, tables=[replace(table, rows=new_rows)])])
    schema = replace(SCHEMA, columns=(SCHEMA.columns[0], OcrColumn("reference", ("Referência",)), SCHEMA.columns[2]))
    structure = reconstruct_ocr_table(source, schema)
    assert len(structure.rows) == 3 and structure.rows[0].cells["reference"].text == "Crédito"


@pytest.mark.parametrize("token", ["R$ - 5,00", "R$ 5,00 -", "R$ -5,00 -"])
def test_explicit_signs_survive_ocr_token_spacing_without_changing_digits(token):
    source = evidence(tables=True)
    page = source.pages[0]
    table = page.tables[0]
    changed = [*table.rows[2][:-1], replace(table.rows[2][-1], text=token)]
    source = replace(source, pages=[replace(page, tables=[replace(table, rows=[*table.rows[:2], changed, table.rows[3]])])])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert [r.amount for r in result.movements] == [Decimal("20.00"), Decimal("-5.00"), Decimal("-5.00")]
    assert result.balance_matches


def test_conflicting_explicit_signs_remain_unresolved():
    source = evidence(tables=True)
    page = source.pages[0]
    table = page.tables[0]
    changed = [*table.rows[2][:-1], replace(table.rows[2][-1], text="R$ +5,00 -")]
    source = replace(source, pages=[replace(page, tables=[replace(table, rows=[*table.rows[:2], changed, table.rows[3]])])])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert result.requires_review and len(result.unresolved_rows) == 1


def test_low_ocr_confidence_requires_review_even_with_matching_balances():
    source = evidence(tables=True)
    page = source.pages[0]
    table = page.tables[0]
    changed = [*table.rows[1][:-1], replace(table.rows[1][-1], confidence=40)]
    source = replace(source, pages=[replace(page, tables=[replace(table, rows=[table.rows[0], changed, *table.rows[2:]])])])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert result.balance_matches and result.requires_review
    assert any(issue.code == "critical_confidence_unconfirmed" for issue in result.issues)


def test_an_unreadable_last_date_cannot_silently_drop_a_readable_amount():
    source = evidence()
    page = source.pages[0]
    last_date_id = next(word.id for word in reversed(page.words) if word.text == "02/01/2026")
    page = replace(page, words=[replace(word, text="??/01/2026") if word.id == last_date_id else word for word in page.words])
    source = replace(source, pages=[page])
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert result.requires_review
    assert any(issue.code == "unassigned_column_anchor" for issue in result.issues)


def test_a_headerless_first_page_still_preserves_explicit_date_and_amount_columns():
    source = evidence(header=False)
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert len(result.movements) == 3 and result.balance_matches


def test_low_confidence_printed_controls_cannot_silently_certify_the_statement():
    source = evidence()
    page = source.pages[0]
    source = replace(
        source, pages=[replace(page, words=[replace(word, confidence=30) if word.text == "110,00" else word for word in page.words])]
    )
    result = interpret_cash_table(source, reconstruct_ocr_table(source, SCHEMA), POLICY)
    assert result.balance_matches and result.requires_review
    assert any(issue.code == "control_confidence_unconfirmed" for issue in result.issues)
