from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DocumentPreflightResult:
    scanned_likely: bool | None
    estimated_pages_count: int | None


@dataclass(frozen=True, slots=True)
class DocumentPreflightPolicy:
    max_upload_size_bytes: int
    max_pages_per_file: int | None
    ocr_max_pages: int
