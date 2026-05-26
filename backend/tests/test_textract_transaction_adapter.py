import pytest

from app.application.document_extraction_models import (
    ExtractedCell,
    ExtractedLine,
    ExtractedPage,
    ExtractedTable,
    RawDocumentExtraction,
)
from app.application.errors import InvalidFileContentError
from app.application.textract_transaction_adapter import adapt_textract_extraction_to_transactions


def test_adapter_extracts_transactions_from_table_header_with_accents() -> None:
    extraction = RawDocumentExtraction(
        provider="aws_textract",
        document_hash="h1",
        pages=[
            ExtractedPage(
                page_number=1,
                tables=[
                    ExtractedTable(
                        id="t1",
                        page_number=1,
                        table_index=1,
                        rows=[
                            [
                                ExtractedCell(1, 1, "Data"),
                                ExtractedCell(1, 2, "Descrição"),
                                ExtractedCell(1, 3, "Crédito"),
                                ExtractedCell(1, 4, "Débito"),
                            ],
                            [
                                ExtractedCell(2, 1, "01/04/2026"),
                                ExtractedCell(2, 2, "PIX RECEBIDO"),
                                ExtractedCell(2, 3, "10,00"),
                                ExtractedCell(2, 4, ""),
                            ],
                        ],
                    )
                ],
            )
        ],
        metrics={},
    )
    result = adapt_textract_extraction_to_transactions(extraction)

    assert len(result.transactions) == 1
    assert result.transactions[0].amount == 10.0
    assert result.canonical_transactions[0].source_parser == "textract_table"


def test_adapter_extracts_transactions_from_table_header_without_accents() -> None:
    extraction = RawDocumentExtraction(
        provider="aws_textract",
        document_hash="h1b",
        pages=[
            ExtractedPage(
                page_number=1,
                tables=[
                    ExtractedTable(
                        id="t1b",
                        page_number=1,
                        table_index=1,
                        rows=[
                            [
                                ExtractedCell(1, 1, "Data"),
                                ExtractedCell(1, 2, "Descricao"),
                                ExtractedCell(1, 3, "Valor"),
                            ],
                            [
                                ExtractedCell(2, 1, "02/04/2026"),
                                ExtractedCell(2, 2, "TED ENVIADA"),
                                ExtractedCell(2, 3, "-25,00"),
                            ],
                        ],
                    )
                ],
            )
        ],
        metrics={},
    )
    result = adapt_textract_extraction_to_transactions(extraction)

    assert len(result.transactions) == 1
    assert result.transactions[0].amount == -25.0
    assert result.canonical_transactions[0].source_parser == "textract_table"


def test_adapter_falls_back_to_table_row_candidate_when_header_missing() -> None:
    extraction = RawDocumentExtraction(
        provider="aws_textract",
        document_hash="h2",
        pages=[
            ExtractedPage(
                page_number=1,
                tables=[
                    ExtractedTable(
                        id="t2",
                        page_number=1,
                        table_index=1,
                        rows=[
                            [
                                ExtractedCell(1, 1, "05/04/2026"),
                                ExtractedCell(1, 2, "TARIFA BANCARIA"),
                                ExtractedCell(1, 3, "-8,90"),
                            ]
                        ],
                    )
                ],
            )
        ],
        metrics={},
    )
    result = adapt_textract_extraction_to_transactions(extraction)

    assert len(result.transactions) == 1
    assert result.canonical_transactions[0].source_parser == "textract_table_row"
    assert "textract_table_row_candidate" in result.canonical_transactions[0].warnings


def test_adapter_falls_back_to_line_window_candidate() -> None:
    extraction = RawDocumentExtraction(
        provider="aws_textract",
        document_hash="h3",
        pages=[
            ExtractedPage(
                page_number=1,
                lines=[
                    ExtractedLine(id="l1", page_number=1, line_index=1, text="06/04/2026"),
                    ExtractedLine(id="l2", page_number=1, line_index=2, text="TRANSFERENCIA CLIENTE"),
                    ExtractedLine(id="l3", page_number=1, line_index=3, text="1.250,00"),
                ],
            )
        ],
        metrics={},
    )
    result = adapt_textract_extraction_to_transactions(extraction)

    assert len(result.transactions) == 1
    assert result.canonical_transactions[0].source_parser == "textract_line_window"
    assert "manual_review_recommended" in result.canonical_transactions[0].warnings


def test_adapter_raises_friendly_error_when_no_candidate_found() -> None:
    extraction = RawDocumentExtraction(provider="aws_textract", document_hash="h4", pages=[ExtractedPage(page_number=1)], metrics={})
    with pytest.raises(InvalidFileContentError):
        adapt_textract_extraction_to_transactions(extraction)
