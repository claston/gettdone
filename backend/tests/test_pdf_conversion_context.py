from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from app.application.models import NormalizedTransaction
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseResult
from app.application.parsers.pdf.models import _ParsedTransaction


@pytest.mark.parametrize("reject_first", [False, True])
def test_concurrent_pdf_conversions_keep_their_own_reference_date(monkeypatch, reject_first: bool) -> None:
    """Pause both calls before layout dispatch; never rely on thread timing."""
    first_entered = Event()
    second_entered = Event()
    first_finished = Event()
    references = {b"FIRST": (1, 2024), b"SECOND": (2, 2025)}
    parse_layout = pdf_parser._parse_layout_specific_statement_rows

    def interleaved_layout_parse(**kwargs):
        first = kwargs["lines"][0].text == "FIRST"
        if first:
            first_entered.set()
            assert second_entered.wait(10), "Second conversion did not reach layout parsing"
        else:
            second_entered.set()
            assert first_finished.wait(10), "First conversion did not finish"
        if first and reject_first:
            raise InvalidFileContentError("rejected first document")
        return parse_layout(**kwargs)

    class DateFromReferenceParser:
        def parse(self, *, layout_name, lines, context):
            month, year = context.reference_month_year or (1, 2000)
            return LayoutSpecificParseResult(
                rows=[
                    _ParsedTransaction(
                        transaction=NormalizedTransaction(
                            date=f"{year}-{month:02d}-01", description=lines[0].text, amount=10.0, type="inflow"
                        ),
                        source_page=1,
                        source_line=1,
                    )
                ],
                selected_parser="reference_test",
                selection_reason="reference_date",
            )

    monkeypatch.setattr(pdf_parser.text_extraction, "read_pdf_creation_month_year", references.__getitem__)
    monkeypatch.setattr(pdf_parser, "_read_native_pdf_page_texts", lambda raw: [raw.decode()])
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts", lambda raw: [raw.decode()])
    monkeypatch.setattr(pdf_parser, "_read_layout_native_pdf_page_texts", lambda raw: [])
    monkeypatch.setattr(pdf_parser, "is_textract_enabled", lambda: False)
    monkeypatch.setattr(pdf_parser, "is_pdf_ocr_enabled", lambda: False)
    monkeypatch.setattr(pdf_parser, "_parse_layout_specific_statement_rows", interleaved_layout_parse)
    monkeypatch.setattr(pdf_parser, "DEFAULT_PDF_LAYOUT_PARSER_REGISTRY", DateFromReferenceParser())

    def convert_first():
        try:
            return pdf_parser.parse_pdf_transactions(b"FIRST")
        finally:
            first_finished.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(convert_first)
        assert first_entered.wait(10)
        second = executor.submit(pdf_parser.parse_pdf_transactions, b"SECOND")
        if reject_first:
            with pytest.raises(InvalidFileContentError, match="rejected first document"):
                first.result(timeout=15)
        else:
            assert first.result(timeout=15).transactions[0].date == "2024-01-01"
        assert second.result(timeout=15).transactions[0].date == "2025-02-01"
