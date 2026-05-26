from app.application.textract_extraction_mapper import map_textract_blocks_to_extraction


def test_map_textract_blocks_to_extraction_builds_lines_and_table_cells() -> None:
    blocks = [
        {"BlockType": "LINE", "Id": "line-1", "Page": 1, "Text": "01/04 PIX RECEBIDO 10,00", "Confidence": 98.2},
        {
            "BlockType": "TABLE",
            "Id": "table-1",
            "Page": 1,
            "Relationships": [{"Type": "CHILD", "Ids": ["cell-1", "cell-2", "cell-3", "cell-4"]}],
        },
        {"BlockType": "CELL", "Id": "cell-1", "RowIndex": 1, "ColumnIndex": 1, "Relationships": [{"Type": "CHILD", "Ids": ["word-1"]}]},
        {"BlockType": "CELL", "Id": "cell-2", "RowIndex": 1, "ColumnIndex": 2, "Relationships": [{"Type": "CHILD", "Ids": ["word-2"]}]},
        {"BlockType": "CELL", "Id": "cell-3", "RowIndex": 2, "ColumnIndex": 1, "Relationships": [{"Type": "CHILD", "Ids": ["word-3"]}]},
        {"BlockType": "CELL", "Id": "cell-4", "RowIndex": 2, "ColumnIndex": 2, "Relationships": [{"Type": "CHILD", "Ids": ["word-4"]}]},
        {"BlockType": "WORD", "Id": "word-1", "Text": "Data"},
        {"BlockType": "WORD", "Id": "word-2", "Text": "Descricao"},
        {"BlockType": "WORD", "Id": "word-3", "Text": "01/04/2026"},
        {"BlockType": "WORD", "Id": "word-4", "Text": "PIX"},
    ]
    extraction = map_textract_blocks_to_extraction(document_hash="abc123", blocks=blocks, page_count=1)

    assert extraction.provider == "aws_textract"
    assert extraction.metrics["line_count"] == 1
    assert extraction.metrics["table_count"] == 1
    assert len(extraction.pages) == 1
    assert extraction.pages[0].lines[0].text == "01/04 PIX RECEBIDO 10,00"
    assert extraction.pages[0].tables[0].rows[0][0].text == "Data"
    assert extraction.pages[0].tables[0].rows[1][1].text == "PIX"


def test_map_textract_blocks_to_extraction_handles_missing_relationships() -> None:
    blocks = [
        {"BlockType": "TABLE", "Id": "table-1", "Page": 1},
        {"BlockType": "LINE", "Id": "line-1", "Page": 2, "Text": "sem tabela"},
    ]

    extraction = map_textract_blocks_to_extraction(document_hash="abc456", blocks=blocks, page_count=2)

    assert extraction.metrics["page_count"] == 2
    assert extraction.metrics["table_count"] == 1
    assert extraction.metrics["cell_count"] == 0
    assert extraction.pages[0].page_number == 1
    assert extraction.pages[1].page_number == 2
