"""Synthetic OCR evidence; no production PDFs, names or account identifiers."""

from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedLine, ExtractedPage, ExtractedWord, RawDocumentExtraction
from app.application.errors import OcrFinancialAmbiguityError


def stone_extraction(kind="missing_fee"):
    lines = []

    def add(text, x, y, width=0.075):
        number = len(lines) + 1
        lines.append(
            ExtractedLine(
                id=f"line-{number}",
                page_number=1,
                line_index=number,
                text=text,
                bbox={"left": x, "top": y, "width": width, "height": 0.008},
                confidence=98,
            )
        )

    add("Extrato de conta corrente", 0.05, 0.03, 0.3)
    add("Stone Instituição de Pagamento S.A.", 0.05, 0.06, 0.4)
    add("Período: de 01/04/2026 a 30/04/2026", 0.05, 0.09, 0.4)
    for label, x in (("DATA", 0.05), ("TIPO", 0.125), ("DESCRIÇÃO", 0.20), ("VALOR", 0.48), ("SALDO", 0.59), ("CONTRAPARTE", 0.70)):
        add(label, x, 0.14)
    if kind == "type_after_name":
        records = [
            ("Entrada", "Antecipação", "19,63", "4.884,67"),
            ("Saída", "Transferência Pix", "102,00", "4.865,04"),
            ("Saída", "Tarifa", "0,03", "4.967,04"),
            ("Entrada", "Pix Maquininha", "5,99", "4.967,04"),
            ("Entrada", "Antecipação", "20,00", "4.961,08"),
        ]
    elif kind == "malformed_balance":
        records = [
            ("Entrada", "Antecipação", "40,53", "10.168,87"),
            ("Saída", "Tarifa", "0,34", "0.128,34"),
            ("Entrada", "Pix Maquininha", "52,99", "10.128,34"),
            ("Entrada", "Antecipação", "121,62", "10.075,69"),
            ("Entrada", "Antecipação", "297,56", "9.954,07"),
        ]
    elif kind == "older_fee_pair":
        records = [
            ("Entrada", "Antecipação", "145,85", "5.735,76"),
            ("Saída", "Tarifa", None, "5.589,91"),
            ("Entrada", "Pix Maquininha", "23,99", "5.589,91"),
            ("Saída", "Tarifa", "0,12", "5.566,07"),
            ("Entrada", "Pix Maquininha", "19,99", "5.566,07"),
            ("Entrada", "Antecipação", "20,00", "5.546,20"),
        ]
    else:
        records = [
            ("Entrada", "Antecipação", "145,85", "5.735,76"),
            ("Saída", "Tarifa", None, "5.589,91"),
            ("Entrada", "Pix Maquininha", "23,99", "5.589,91"),
            ("Entrada", "Antecipação", "52,03", "5.566,07"),
            ("Entrada", "Antecipação", "19,63", "5.514,04"),
        ]
    for index, (direction, description, amount, balance) in enumerate(records):
        y = 0.23 + index * 0.12
        add("23/04/26", 0.05, y)
        if kind == "type_after_name" and index == 1:
            add("CLIENTE EXEMPLO", 0.20, y - 0.006, 0.2)
        add(direction, 0.125, y)
        add(description, 0.20, y + 0.006, 0.2)
        add("R$" if amount is None else f"R$ {amount}", 0.48, y + 0.003)
        add(f"R$ {balance}", 0.59, y + 0.004)
    return RawDocumentExtraction(
        provider="aws_textract", document_hash="synthetic-stone", pages=[ExtractedPage(page_number=1, lines=lines)], metrics={}
    )


def parse(extraction):
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)


def test_missing_fee_cell_can_be_recovered_across_page_boundary_with_complete_ledger_witnesses():
    extraction = stone_extraction()
    lines = extraction.pages[0].lines
    # First three records on one page, remaining independent witnesses on the next.
    split = next(i for i, line in enumerate(lines) if line.text == "23/04/26" and line.bbox["top"] > 0.5)
    first = [line for line in lines[:split] if line.text != "R$"]
    second = [*lines[:9], *lines[split:]]
    pages = [
        replace(extraction.pages[0], lines=first),
        replace(extraction.pages[0], page_number=2, lines=[replace(line, page_number=2) for line in second]),
    ]
    result = parse(replace(extraction, pages=pages))
    fee = result.canonical_transactions[1]
    assert fee.amount == -0.15
    assert {page for page, _ in fee.source_line_refs} == {1, 2}
    assert {region.page for region in fee.source_regions} == {1, 2}


def change_line(extraction, before, after, occurrence=0):
    lines = list(extraction.pages[0].lines)
    matches = [index for index, line in enumerate(lines) if line.text == before]
    index = matches[occurrence]
    lines[index] = replace(lines[index], text=after)
    return replace(extraction, pages=[replace(extraction.pages[0], lines=lines)])


def test_standalone_type_after_counterparty_still_marks_debit_without_changing_description():
    result = parse(stone_extraction("type_after_name"))
    row = result.canonical_transactions[1]
    assert row.amount == -102 and row.type == "outflow"
    assert "CLIENTE EXEMPLO" in row.description
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_missing_fee_uses_its_own_balance_and_verified_credit_pair():
    result = parse(stone_extraction())
    assert len(result.transactions) == 5
    row = result.canonical_transactions[1]
    assert row.amount == -0.15 and row.running_balance == 5589.91
    assert row.description == "Saída Tarifa"
    assert "ocr_amount_recovered_from_balances" in row.warnings
    assert row.source_regions and len(row.source_line_refs) >= 5
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.source_extraction == stone_extraction()


def test_recovered_fee_with_an_older_fee_credit_pair_requires_its_independent_next_balance():
    result = parse(stone_extraction("older_fee_pair"))
    assert result.canonical_transactions[1].amount == -0.15
    assert result.parse_metrics["balance_consistency_failed"] == 0
    broken = change_line(stone_extraction("older_fee_pair"), "R$ 5.546,20", "R$ 5.546,21")
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(broken)


def test_invalid_leading_zero_balance_is_recovered_only_with_independent_witnesses():
    extraction = stone_extraction("malformed_balance")
    result = parse(extraction)
    row = result.canonical_transactions[1]
    assert row.amount == -0.34 and row.running_balance == 10128.34
    assert "ocr_balance_recovered_from_context" in row.warnings
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.source_extraction == extraction
    assert any(line.text == "R$ 0.128,34" for line in extraction.pages[0].lines)


@pytest.mark.parametrize(
    "before,after",
    [
        ("R$ 5.589,91", "R$ 5.580,00"),
        ("R$ 5.735,76", "R$ 5.736,76"),
        ("R$ 5.514,04", "R$ 5.513,04"),
        ("Pix Maquininha", "Transferência Pix"),
        ("Tarifa", "Pagamento"),
    ],
)
def test_missing_value_with_inconsistent_or_unrelated_context_requires_review(before, after):
    extraction = change_line(stone_extraction(), before, after)
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(extraction)


def test_recovery_does_not_replace_an_explicit_printed_amount():
    extraction = change_line(stone_extraction(), "R$", "R$ 1,15")
    result = parse(extraction)
    assert result.transactions[1].amount == -1.15
    assert "ocr_amount_recovered_from_balances" not in result.canonical_transactions[1].warnings
    assert result.parse_metrics["balance_consistency_failed"] > 0


@pytest.mark.parametrize("raw", ["RS 1,15", "R$ 1.15", "R$ $1,15"])
def test_currency_ocr_noise_and_decimal_separator_preserve_printed_amount(raw):
    result = parse(change_line(stone_extraction(), "R$", raw))
    assert result.transactions[1].amount == -1.15
    assert "ocr_amount_recovered_from_balances" not in result.canonical_transactions[1].warnings
    assert result.parse_metrics["balance_consistency_failed"] > 0


def test_missing_value_at_page_boundary_cannot_borrow_another_page_balance():
    extraction = stone_extraction()
    first, second = extraction.pages[0].lines[:19], extraction.pages[0].lines[19:]
    second = [replace(line, page_number=2, line_index=index + 1) for index, line in enumerate(second)]
    extraction = replace(extraction, pages=[ExtractedPage(page_number=1, lines=first), ExtractedPage(page_number=2, lines=second)])
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(extraction)


@pytest.mark.parametrize("before,after", [("R$ 10.168,87", "R$ 10.169,87"), ("R$ 10.075,69", "R$ 10.076,69")])
def test_malformed_balance_without_consistent_neighbours_cannot_be_completed(before, after):
    extraction = change_line(stone_extraction("malformed_balance"), before, after)
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(extraction)


@pytest.mark.parametrize("label", ["CONTRAPARTE", "TIPO"])
def test_missing_columns_cannot_turn_a_balance_into_a_movement(label):
    extraction = change_line(stone_extraction(), label, "COLUNA ILEGÍVEL")
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(extraction)


@pytest.mark.parametrize("marker", ["R$", "$"])
def test_missing_geometry_cannot_turn_a_balance_into_a_movement(marker):
    extraction = change_line(stone_extraction(), "R$", marker)
    extraction = replace(
        extraction, pages=[replace(extraction.pages[0], lines=[replace(line, bbox=None) for line in extraction.pages[0].lines])]
    )
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(extraction)


def test_recovery_does_not_cross_dates():
    extraction = change_line(stone_extraction(), "23/04/26", "22/04/26", occurrence=4)
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(extraction)


@pytest.mark.parametrize("raw", ["R$", "R$ 0,15"])
def test_handwritten_amount_is_not_used_as_printed_evidence(raw):
    extraction = change_line(stone_extraction(), "R$", raw)
    page = extraction.pages[0]
    line = next(line for line in page.lines if line.text == raw)
    page = replace(
        page,
        lines=[replace(item, word_ids=("handwritten-value",)) if item == line else item for item in page.lines],
        words=[ExtractedWord(id="handwritten-value", page_number=1, word_index=1, text="0,15", text_type="HANDWRITING")],
    )
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[page]))


def test_two_amounts_in_one_cell_are_not_resolved_by_balance():
    extraction = change_line(stone_extraction(), "R$", "R$ 0,15")
    page = extraction.pages[0]
    source = next(line for line in page.lines if line.text == "R$ 0,15")
    duplicate = replace(source, id="extra-value", line_index=100, text="R$ 1,15")
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[replace(page, lines=[*page.lines, duplicate])]))


def test_partially_read_digits_beside_currency_marker_are_not_replaced_by_inference():
    extraction = stone_extraction()
    page = extraction.pages[0]
    source = next(line for line in page.lines if line.text == "R$")
    partial = replace(source, id="partial-value", line_index=100, text="0,1?")
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[replace(page, lines=[*page.lines, partial])]))


def test_known_type_column_does_not_read_counterparty_name_as_direction():
    extraction = change_line(stone_extraction("type_after_name"), "CLIENTE EXEMPLO", "ENTRADAS E SAÍDAS LTDA")
    result = parse(extraction)
    assert result.transactions[1].amount == -102


def test_conflicting_direction_in_type_column_is_rejected():
    extraction = stone_extraction("type_after_name")
    page = extraction.pages[0]
    source = next(line for line in page.lines if line.text == "Saída")
    duplicate = replace(source, id="extra-type", line_index=100, text="Entrada")
    with pytest.raises(OcrFinancialAmbiguityError):
        parse(replace(extraction, pages=[replace(page, lines=[*page.lines, duplicate])]))
