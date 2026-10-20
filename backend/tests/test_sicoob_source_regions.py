from contextlib import closing
from io import BytesIO

import pypdfium2 as pdfium
import pytest
from pypdf import PdfReader, PdfWriter

from app.application.pdf_parser import parse_pdf_transactions
from tests.fixtures.sicoob_source_pdf import sicoob_source_pdf


@pytest.mark.parametrize("columns", [True, False])
def test_sicoob_highlights_follow_real_rows_regardless_of_internal_column_order(columns, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    parsed = parse_pdf_transactions(sicoob_source_pdf(columns=columns))
    assert [row.amount for row in parsed.transactions] == [500, -100, -100, -100, 8625]
    rows = parsed.canonical_transactions
    assert all(row.source_regions for row in rows)
    for row, y in zip(rows, [635, 615, 595, 555, 515], strict=True):
        region = row.source_regions[0]
        assert region.page == 1
        assert region.left < .06
        assert region.left + region.width > .8
        assert region.top <= (800-y)/800 <= region.top + region.height + .005
    assert [row.source_regions[0].top for row in rows] == sorted(row.source_regions[0].top for row in rows)


def test_sicoob_column_order_fixture_changes_no_visible_pixels():
    pixels = []
    for columns in (False, True):
        with pdfium.PdfDocument(sicoob_source_pdf(columns=columns)) as document:
            with closing(document[0]) as page, closing(page.render(scale=1)) as bitmap:
                pixels.append(bitmap.to_pil().tobytes())
    assert pixels[0] == pixels[1]


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_sicoob_spatial_reassembly_preserves_original_crop_and_rotation(rotation, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    writer = PdfWriter()
    page = writer.add_page(PdfReader(BytesIO(sicoob_source_pdf())).pages[0])
    page.cropbox.lower_left, page.cropbox.upper_right = (10, 10), (590, 790)
    page.rotate(rotation)
    output = BytesIO()
    writer.write(output)
    parsed = parse_pdf_transactions(output.getvalue())
    assert len(parsed.transactions) == 5
    assert all(row.source_regions for row in parsed.canonical_transactions)
    original = parse_pdf_transactions(sicoob_source_pdf())
    assert parsed.transactions == original.transactions
    # The first evidence is horizontal at 180°, and vertical at 90°/270°.
    first = parsed.canonical_transactions[0].source_regions[0]
    assert (first.width > first.height) == (rotation == 180)
