from __future__ import annotations

import hashlib
import math
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO

from app.application.document_extraction_models import (
    ExtractedLine,
    ExtractedPage,
    ExtractedWord,
    RawDocumentExtraction,
)


@dataclass(frozen=True, slots=True)
class GeometryLineMetrics:
    fragment_merges: int = 0


@dataclass(frozen=True, slots=True)
class NativeGeometryMetrics:
    page_count: int
    word_count: int
    line_count: int
    duplicate_characters_removed: int
    fragment_merges: int
    rotated_word_count: int


@dataclass(frozen=True, slots=True)
class NativeGeometryExtraction:
    document: RawDocumentExtraction
    page_texts: tuple[str, ...]
    metrics: NativeGeometryMetrics


@dataclass(slots=True)
class _LineCluster:
    page_number: int
    words: list[ExtractedWord]
    center: float
    height: float


def normalize_geometry_text(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    cleaned = "".join(" " if char.isspace() else char for char in normalized if unicodedata.category(char) != "Cf")
    return " ".join(cleaned.split())


def reconstruct_geometry_lines(
    words: tuple[ExtractedWord, ...] | list[ExtractedWord],
) -> tuple[tuple[ExtractedLine, ...], GeometryLineMetrics]:
    clusters: list[_LineCluster] = []
    for word in sorted(words, key=lambda item: (item.page_number, item.rotation, _bbox_top(item), _bbox_left(item))):
        if not word.normalized_text:
            continue
        selected: _LineCluster | None = None
        selected_distance = math.inf
        word_center = _bbox_top(word) + (_bbox_height(word) / 2)
        for cluster in clusters:
            if cluster.page_number != word.page_number:
                continue
            if cluster.words and cluster.words[0].rotation != word.rotation:
                continue
            tolerance = max(0.002, max(cluster.height, _bbox_height(word)) * 0.55)
            distance = abs(cluster.center - word_center)
            if distance <= tolerance and distance < selected_distance:
                selected = cluster
                selected_distance = distance
        if selected is None:
            clusters.append(
                _LineCluster(
                    page_number=word.page_number,
                    words=[word],
                    center=word_center,
                    height=_bbox_height(word),
                )
            )
            continue
        selected.words.append(word)
        selected.center = statistics.median(
            _bbox_top(item) + (_bbox_height(item) / 2) for item in selected.words
        )
        selected.height = statistics.median(_bbox_height(item) for item in selected.words)

    fragment_merges = 0
    lines: list[ExtractedLine] = []
    line_counts_by_page: dict[int, int] = defaultdict(int)
    for cluster in sorted(
        clusters,
        key=lambda item: (item.page_number, item.center, min(_bbox_left(word) for word in item.words)),
    ):
        line_counts_by_page[cluster.page_number] += 1
        line_index = line_counts_by_page[cluster.page_number]
        ordered = tuple(sorted(cluster.words, key=_bbox_left))
        projected_text, projected_merges = _project_line_words(ordered, normalized=False)
        normalized_text, _ = _project_line_words(ordered, normalized=True)
        fragment_merges += projected_merges
        lines.append(
            ExtractedLine(
                id=f"p{cluster.page_number}-l{line_index}",
                page_number=cluster.page_number,
                line_index=line_index,
                text=projected_text,
                normalized_text=normalized_text,
                bbox=_union_bbox(ordered),
                source_word_ids=tuple(word.id for word in ordered),
                words=ordered,
            )
        )
    return tuple(lines), GeometryLineMetrics(fragment_merges=fragment_merges)


def extract_native_pdf_geometry(raw_bytes: bytes) -> NativeGeometryExtraction:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("pdfplumber is required for native geometry extraction") from exc

    pages: list[ExtractedPage] = []
    duplicate_characters_removed = 0
    fragment_merges = 0
    rotated_word_count = 0
    with pdfplumber.open(BytesIO(raw_bytes), unicode_norm=None) as pdf:
        for page_number, source_page in enumerate(pdf.pages, start=1):
            page_width = max(float(source_page.width or 0.0), 1.0)
            page_height = max(float(source_page.height or 0.0), 1.0)
            original_char_count = len(source_page.chars)
            median_char_height = _median_pdfplumber_char_height(source_page.chars)
            dedupe_tolerance = max(0.25, median_char_height * 0.08)
            deduped_page = source_page.dedupe_chars(
                tolerance=dedupe_tolerance,
                extra_attrs=("fontname", "size"),
            )
            duplicate_characters_removed += max(0, original_char_count - len(deduped_page.chars))
            y_tolerance = max(0.75, median_char_height * 0.22)
            extracted_words = deduped_page.extract_words(
                x_tolerance=1.0,
                x_tolerance_ratio=0.22,
                y_tolerance=y_tolerance,
                keep_blank_chars=False,
                use_text_flow=False,
                extra_attrs=["size"],
                split_at_punctuation=False,
                expand_ligatures=True,
                return_chars=True,
            )
            words = [
                _map_pdfplumber_word(
                    raw_word,
                    page_number=page_number,
                    word_index=word_index,
                    page_width=page_width,
                    page_height=page_height,
                )
                for word_index, raw_word in enumerate(extracted_words, start=1)
                if normalize_geometry_text(raw_word.get("text"))
            ]
            rotated_word_count += sum(word.rotation != 0 for word in words)
            lines, line_metrics = reconstruct_geometry_lines(words)
            fragment_merges += line_metrics.fragment_merges
            pages.append(
                ExtractedPage(
                    page_number=page_number,
                    lines=list(lines),
                    width=page_width,
                    height=page_height,
                    words=words,
                )
            )

    page_texts = tuple(
        "\n".join(line.text for line in page.lines if line.text.strip())
        for page in pages
    )
    metrics = NativeGeometryMetrics(
        page_count=len(pages),
        word_count=sum(len(page.words) for page in pages),
        line_count=sum(len(page.lines) for page in pages),
        duplicate_characters_removed=duplicate_characters_removed,
        fragment_merges=fragment_merges,
        rotated_word_count=rotated_word_count,
    )
    document = RawDocumentExtraction(
        provider="pdfplumber_native",
        document_hash=hashlib.sha256(raw_bytes).hexdigest(),
        pages=pages,
        metrics={
            "page_count": metrics.page_count,
            "word_count": metrics.word_count,
            "line_count": metrics.line_count,
            "duplicate_characters_removed": metrics.duplicate_characters_removed,
            "fragment_merges": metrics.fragment_merges,
            "rotated_word_count": metrics.rotated_word_count,
        },
    )
    return NativeGeometryExtraction(document=document, page_texts=page_texts, metrics=metrics)


def _map_pdfplumber_word(
    raw_word: dict[str, object],
    *,
    page_number: int,
    word_index: int,
    page_width: float,
    page_height: float,
) -> ExtractedWord:
    text = str(raw_word.get("text") or "")
    chars = list(raw_word.get("chars") or [])
    normalized_text = normalize_geometry_text(text)
    left = _clamp01(_number(raw_word.get("x0")) / page_width)
    right = _clamp01(_number(raw_word.get("x1")) / page_width)
    top = _clamp01(_number(raw_word.get("top")) / page_height)
    bottom = _clamp01(_number(raw_word.get("bottom")) / page_height)
    upright = bool(raw_word.get("upright", True))
    return ExtractedWord(
        id=f"p{page_number}-w{word_index}",
        page_number=page_number,
        word_index=word_index,
        text=text,
        normalized_text=normalized_text,
        bbox={
            "left": left,
            "top": top,
            "width": max(0.0, right - left),
            "height": max(0.0, bottom - top),
        },
        font_size=_optional_number(raw_word.get("size")),
        rotation=0 if upright else 90,
        source_fragment_ids=tuple(f"p{page_number}-w{word_index}-c{index}" for index, _char in enumerate(chars, start=1)),
    )


def _project_line_words(words: tuple[ExtractedWord, ...], *, normalized: bool) -> tuple[str, int]:
    if not words:
        return "", 0
    char_widths = [
        _bbox_width(word) / max(1, len(word.normalized_text))
        for word in words
        if _bbox_width(word) > 0 and word.normalized_text
    ]
    median_char_width = statistics.median(char_widths) if char_widths else 0.01
    pieces: list[str] = []
    fragment_merges = 0
    previous: ExtractedWord | None = None
    for word in words:
        value = word.normalized_text if normalized else word.text
        if previous is not None:
            gap = max(0.0, _bbox_left(word) - (_bbox_left(previous) + _bbox_width(previous)))
            if _should_merge_fragments(previous, word, gap=gap, median_char_width=median_char_width):
                separator = ""
                fragment_merges += 1
            elif gap <= median_char_width * 2.5:
                separator = " "
            else:
                separator = " " * min(25, max(2, round(gap / max(median_char_width, 0.0001))))
            pieces.append(separator)
        pieces.append(value)
        previous = word
    return "".join(pieces).rstrip(), fragment_merges


def _should_merge_fragments(
    left: ExtractedWord,
    right: ExtractedWord,
    *,
    gap: float,
    median_char_width: float,
) -> bool:
    if gap > median_char_width * 0.2:
        return False
    if left.rotation != right.rotation:
        return False
    if left.font_size is not None and right.font_size is not None:
        if abs(left.font_size - right.font_size) > max(left.font_size, right.font_size) * 0.08:
            return False
    return left.normalized_text[-1:].isalnum() and right.normalized_text[:1].isalnum()


def _union_bbox(words: tuple[ExtractedWord, ...]) -> dict[str, float] | None:
    if not words:
        return None
    left = min(_bbox_left(word) for word in words)
    top = min(_bbox_top(word) for word in words)
    right = max(_bbox_left(word) + _bbox_width(word) for word in words)
    bottom = max(_bbox_top(word) + _bbox_height(word) for word in words)
    return {"left": left, "top": top, "width": right - left, "height": bottom - top}


def _median_pdfplumber_char_height(chars: list[dict[str, object]]) -> float:
    heights = [abs(_number(char.get("bottom")) - _number(char.get("top"))) for char in chars]
    positive = [height for height in heights if height > 0]
    return statistics.median(positive) if positive else 10.0


def _number(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _optional_number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _bbox_left(word: ExtractedWord) -> float:
    return float(word.bbox.get("left", 0.0))


def _bbox_top(word: ExtractedWord) -> float:
    return float(word.bbox.get("top", 0.0))


def _bbox_width(word: ExtractedWord) -> float:
    return float(word.bbox.get("width", 0.0))


def _bbox_height(word: ExtractedWord) -> float:
    return float(word.bbox.get("height", 0.0))
