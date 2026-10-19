"""Normalized rectangles on the original PDF, after intrinsic rotation and crop."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class SourceRegion:
    page: int
    left: float
    top: float
    width: float
    height: float

    def __post_init__(self):
        if type(self.page) is not int or self.page < 1:
            raise ValueError("Invalid source page")
        values = (self.left, self.top, self.width, self.height)
        if any(type(v) not in (int, float) or not isfinite(v) for v in values):
            raise ValueError("Nonfinite source rectangle")
        if (self.left < 0 or self.top < 0 or self.width <= 0 or self.height <= 0
                or self.left + self.width > 1.000001 or self.top + self.height > 1.000001):
            raise ValueError("Source rectangle outside page")


def parse_source_regions(value: object) -> list[SourceRegion]:
    """Old or partial JSON must remain reviewable even without geometry."""
    if not isinstance(value, (list, tuple)):
        return []
    regions = []
    for item in value:
        try:
            region = item if isinstance(item, SourceRegion) else SourceRegion(**{
                key: item[key] for key in ("page", "left", "top", "width", "height")
            })
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if region not in regions:
            regions.append(region)
    return regions


def validate_review_regions(value: object, page_count: int | None = None) -> list[SourceRegion]:
    """Reject a malformed reviewer write entirely; extraction evidence stays immutable."""
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("Informe até 50 marcações por lançamento.")
    regions = []
    for item in value:
        try:
            region = item if isinstance(item, SourceRegion) else SourceRegion(**{
                key: item[key] for key in ("page", "left", "top", "width", "height")
            })
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Marcação fora dos limites do PDF.") from exc
        if region.page > (page_count if type(page_count) is int and page_count > 0 else 10000):
            raise ValueError("Página da marcação não existe no PDF.")
        if region not in regions:
            regions.append(region)
    return regions


def evidence_regions(page: int, items, *, words=()) -> list[SourceRegion]:
    """Only boxes belonging to evidence actually consumed by a parser."""
    by_id = {word.id: word for word in words}
    regions = []
    for item in items:
        if not str(item.text or "").strip():
            continue
        boxes = [item.bbox] if item.bbox else [
            by_id[word_id].bbox for word_id in item.word_ids if word_id in by_id and by_id[word_id].bbox
        ]
        for box in boxes:
            regions.extend(parse_source_regions([{"page": page, **box}]))
    return list(dict.fromkeys(regions))
