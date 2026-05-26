from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractionWarning:
    code: str
    message: str | None = None


@dataclass(frozen=True)
class ExtractedCell:
    row_index: int
    column_index: int
    text: str
    confidence: float | None = None
    bbox: dict[str, float] | None = None


@dataclass(frozen=True)
class ExtractedTable:
    id: str
    page_number: int
    table_index: int
    rows: list[list[ExtractedCell]]
    confidence: float | None = None


@dataclass(frozen=True)
class ExtractedLine:
    id: str
    page_number: int
    line_index: int
    text: str
    confidence: float | None = None
    bbox: dict[str, float] | None = None


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    lines: list[ExtractedLine] = field(default_factory=list)
    tables: list[ExtractedTable] = field(default_factory=list)
    confidence: float | None = None


@dataclass(frozen=True)
class RawDocumentExtraction:
    provider: str
    document_hash: str
    pages: list[ExtractedPage]
    metrics: dict[str, int | float | str]
    warnings: list[ExtractionWarning] = field(default_factory=list)
