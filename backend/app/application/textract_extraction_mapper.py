from collections import defaultdict

from app.application.document_extraction_models import (
    ExtractedCell,
    ExtractedLine,
    ExtractedPage,
    ExtractedTable,
    RawDocumentExtraction,
)


def map_textract_blocks_to_extraction(
    *,
    document_hash: str,
    blocks: list[dict[str, object]],
    page_count: int | None = None,
) -> RawDocumentExtraction:
    by_id = {str(block.get("Id")): block for block in blocks if block.get("Id")}
    line_blocks = [block for block in blocks if block.get("BlockType") == "LINE"]
    table_blocks = [block for block in blocks if block.get("BlockType") == "TABLE"]

    lines_by_page: dict[int, list[ExtractedLine]] = defaultdict(list)
    for line_index, block in enumerate(line_blocks, start=1):
        page_number = int(block.get("Page") or 1)
        lines_by_page[page_number].append(
            ExtractedLine(
                id=str(block.get("Id") or f"line-{line_index}"),
                page_number=page_number,
                line_index=len(lines_by_page[page_number]) + 1,
                text=str(block.get("Text") or "").strip(),
                confidence=_to_float(block.get("Confidence")),
                bbox=_bbox(block),
            )
        )

    tables_by_page: dict[int, list[ExtractedTable]] = defaultdict(list)
    for table_index, table in enumerate(table_blocks, start=1):
        page_number = int(table.get("Page") or 1)
        rows = _table_rows(table, by_id)
        tables_by_page[page_number].append(
            ExtractedTable(
                id=str(table.get("Id") or f"table-{table_index}"),
                page_number=page_number,
                table_index=len(tables_by_page[page_number]) + 1,
                rows=rows,
                confidence=_to_float(table.get("Confidence")),
            )
        )

    known_pages = set(lines_by_page.keys()) | set(tables_by_page.keys())
    if page_count and page_count > 0:
        known_pages |= set(range(1, page_count + 1))
    pages = [
        ExtractedPage(
            page_number=page,
            lines=lines_by_page.get(page, []),
            tables=tables_by_page.get(page, []),
        )
        for page in sorted(known_pages)
    ]
    metrics: dict[str, int | float | str] = {
        "page_count": len(pages),
        "block_count": len(blocks),
        "line_count": len(line_blocks),
        "table_count": len(table_blocks),
        "cell_count": sum(len(row) for page in pages for table in page.tables for row in table.rows),
    }
    return RawDocumentExtraction(
        provider="aws_textract",
        document_hash=document_hash,
        pages=pages,
        metrics=metrics,
    )


def _table_rows(table: dict[str, object], by_id: dict[str, dict[str, object]]) -> list[list[ExtractedCell]]:
    cell_blocks = []
    for child_id in _relationship_ids(table, "CHILD"):
        child = by_id.get(child_id)
        if child and child.get("BlockType") == "CELL":
            cell_blocks.append(child)

    row_count = max((int(cell.get("RowIndex") or 0) for cell in cell_blocks), default=0)
    column_count = max((int(cell.get("ColumnIndex") or 0) for cell in cell_blocks), default=0)
    grid: dict[tuple[int, int], ExtractedCell] = {}
    for cell in cell_blocks:
        row_index = int(cell.get("RowIndex") or 0)
        column_index = int(cell.get("ColumnIndex") or 0)
        if row_index < 1 or column_index < 1:
            continue
        grid[(row_index, column_index)] = ExtractedCell(
            row_index=row_index,
            column_index=column_index,
            text=_cell_text(cell, by_id),
            confidence=_to_float(cell.get("Confidence")),
            bbox=_bbox(cell),
        )

    rows: list[list[ExtractedCell]] = []
    for row_index in range(1, row_count + 1):
        row: list[ExtractedCell] = []
        for column_index in range(1, column_count + 1):
            row.append(
                grid.get(
                    (row_index, column_index),
                    ExtractedCell(row_index=row_index, column_index=column_index, text=""),
                )
            )
        rows.append(row)
    return rows


def _relationship_ids(block: dict[str, object], relationship_type: str) -> list[str]:
    values: list[str] = []
    for relationship in block.get("Relationships") or []:
        if relationship.get("Type") == relationship_type:
            for item in relationship.get("Ids") or []:
                values.append(str(item))
    return values


def _cell_text(cell: dict[str, object], by_id: dict[str, dict[str, object]]) -> str:
    parts: list[str] = []
    for child_id in _relationship_ids(cell, "CHILD"):
        child = by_id.get(child_id)
        if not child:
            continue
        if child.get("BlockType") == "WORD":
            text = str(child.get("Text") or "").strip()
            if text:
                parts.append(text)
        if child.get("BlockType") == "SELECTION_ELEMENT":
            status = str(child.get("SelectionStatus") or "").strip()
            if status:
                parts.append(f"[{status}]")
    return " ".join(parts)


def _to_float(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _bbox(block: dict[str, object]) -> dict[str, float] | None:
    geometry = block.get("Geometry")
    if not isinstance(geometry, dict):
        return None
    bbox = geometry.get("BoundingBox")
    if not isinstance(bbox, dict):
        return None
    result: dict[str, float] = {}
    for key in ("Left", "Top", "Width", "Height"):
        value = bbox.get(key)
        if isinstance(value, int | float):
            result[key.lower()] = float(value)
    return result or None
