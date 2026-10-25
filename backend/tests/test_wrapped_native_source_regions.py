from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application.transaction_source_regions import bind_transaction_source_regions
from tests.test_transaction_source_regions import parsed


def wrapped_columns_pdf(rotation=0):
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    commands = []
    texts = ["HEADER"]

    def draw(text, x, y, size=10.5):
        commands.append(f"BT /F1 {size} Tf 1 0 0 1 {x} {y} Tm ({text}) Tj ET")

    draw("HEADER", 35, 760)
    for index, y in enumerate([690, 650, 610], 1):
        description = f"PIX RECEBIDO CLIENTE {index}"
        texts.append(f"01/04/2026 {description} EMPRESA SINTETICA 00000000000100 {index}00,00")
        draw("01/04/2026", 35, y)
        draw(description, 100, y + 6.4 if index < 3 else y, 9)
        if index < 3:
            draw(f"REFERENCIA {index}", 100, y - 4.2, 9)
            texts.append(f"REFERENCIA {index}" + (" FILIAL 2" if index == 2 else ""))
        draw("EMPRESA SINTETICA", 260, y + 6.4 if index == 2 else y, 9 if index == 2 else 10.5)
        if index == 2:
            draw("FILIAL 2", 260, y - 4.2, 9)
        draw("00000000000100", 380, y)
        draw(f"{index}00,00", 500, y)
    stream = DecodedStreamObject()
    stream.set_data("\n".join(commands).encode("ascii"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    page.rotate(rotation)
    output = BytesIO()
    writer.write(output)
    return output.getvalue(), texts


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_centered_date_and_amount_with_wrapped_description_keep_first_two_highlights(rotation):
    raw, lines = wrapped_columns_pdf(rotation)
    result = parsed(["\n".join(lines)], [(1, 2), (1, 4), (1, 6)])
    enriched = bind_transaction_source_regions(result, raw)
    assert all(row.source_regions for row in enriched.canonical_transactions)
    first, second, third = [row.source_regions[0] for row in enriched.canonical_transactions]
    assert first != second != third
    assert first.page == second.page == third.page == 1
    if rotation == 0:
        assert first.top < second.top < third.top
        assert first.left < .07 and first.left + first.width > .88
    assert enriched.transactions == result.transactions
    assert [row.source_line for row in enriched.canonical_transactions] == [2, 4, 6]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_layout_text_with_joined_columns_keeps_exact_character_provenance(rotation):
    raw, lines = wrapped_columns_pdf(rotation)
    # Layout extraction can omit the space between the date and description.
    lines = [line.replace("01/04/2026 PIX", "01/04/2026PIX") for line in lines]
    result = parsed(["\n".join(lines)], [(1, 2), (1, 4), (1, 6)])
    enriched = bind_transaction_source_regions(result, raw)
    assert all(row.source_regions for row in enriched.canonical_transactions)
    assert enriched.transactions == result.transactions
    assert len({row.source_regions[0] for row in enriched.canonical_transactions}) == 3


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("header", ["HEADER", "DIFFERENT EXTRACTION HEADER"])
def test_folded_description_lines_bind_only_exact_original_characters(rotation, header):
    raw, _ = wrapped_columns_pdf(rotation)
    lines = [header,
             "01/04/2026PIX RECEBIDO CLIENTE 1REFERENCIA 1 EMPRESA SINTETICA 00000000000100 100,00",
             "01/04/2026PIX RECEBIDO CLIENTE 2REFERENCIA 2EMPRESA SINTETICAFILIAL 2 00000000000100 200,00",
             "01/04/2026PIX RECEBIDO CLIENTE 3 EMPRESA SINTETICA 00000000000100 300,00"]
    result = parsed(["\n".join(lines)], [(1, 2), (1, 3), (1, 4)])
    enriched = bind_transaction_source_regions(result, raw)
    first, second, third = [row.source_regions[0] for row in enriched.canonical_transactions]
    assert len({first, second, third}) == 3
    if rotation == 0:
        assert first.top < second.top < third.top
        assert first.left < .07 and first.left + first.width > .88
        assert first.top + first.height < second.top
    # Text changed by the parser must never be matched approximately.
    changed = parsed([lines[1].replace("100,00", "101,00")], [(1, 1)])
    assert not bind_transaction_source_regions(changed, raw).canonical_transactions[0].source_regions
