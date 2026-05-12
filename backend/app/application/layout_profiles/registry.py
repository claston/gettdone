from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.application.normalization.text import normalize_upper_text

_PROFILE_DIR = Path(__file__).with_name("profiles")
_TEMPLATE_FILENAME = "template_prompt_meta_modelo.yaml"


@dataclass(frozen=True)
class DeclarativeLayoutProfile:
    profile_name: str
    bank: str
    confidence_label: str
    min_score_hint: float
    required_keywords: tuple[str, ...]
    optional_keywords: tuple[str, ...]
    negative_keywords: tuple[str, ...]
    header_keywords: tuple[str, ...]
    source_path: str


@lru_cache(maxsize=1)
def load_layout_profiles() -> tuple[DeclarativeLayoutProfile, ...]:
    profiles: list[DeclarativeLayoutProfile] = []
    if not _PROFILE_DIR.exists():
        return ()

    for path in sorted(_PROFILE_DIR.glob("*.yaml")):
        if path.name == _TEMPLATE_FILENAME:
            continue
        profile = _load_profile(path)
        if profile is not None:
            profiles.append(profile)

    return tuple(profiles)


def score_layout_profile(profile: DeclarativeLayoutProfile, normalized_text: str, *, structure_score: float = 0.0) -> float:
    required_hits, required_ratio = _keyword_hits(profile.required_keywords, normalized_text)
    optional_hits, optional_ratio = _keyword_hits(profile.optional_keywords, normalized_text)
    header_hits, header_ratio = _keyword_hits(profile.header_keywords, normalized_text)
    negative_hits, _negative_ratio = _keyword_hits(profile.negative_keywords, normalized_text)

    if required_hits == 0 and header_hits == 0:
        return 0.0

    score = (required_ratio * 0.64) + (optional_ratio * 0.16) + (header_ratio * 0.14) + min(structure_score, 0.06)

    bank_token = normalize_upper_text(profile.bank)
    if bank_token and bank_token in normalized_text:
        score += 0.04

    if negative_hits:
        score *= max(0.25, 1.0 - min(negative_hits * 0.2, 0.65))

    minimum_required_hits = min(4, max(2, len(profile.required_keywords) // 4))
    if required_hits < minimum_required_hits:
        score *= 0.45

    return min(1.0, score)


def _load_profile(path: Path) -> DeclarativeLayoutProfile | None:
    lines = path.read_text(encoding="utf-8").splitlines()
    layout_lines = _layout_profile_lines(lines)
    if not layout_lines:
        return None

    profile_name = _scalar_value(layout_lines, "profile_name")
    if not profile_name:
        return None

    return DeclarativeLayoutProfile(
        profile_name=profile_name,
        bank=_scalar_value(layout_lines, "bank"),
        confidence_label=_scalar_value(layout_lines, "confidence"),
        min_score_hint=_float_value(_nested_scalar_value(layout_lines, "classifier", "min_score_hint"), default=0.7),
        required_keywords=tuple(_nested_list_values(layout_lines, "classifier", "required_keywords")),
        optional_keywords=tuple(_nested_list_values(layout_lines, "classifier", "optional_keywords")),
        negative_keywords=tuple(_nested_list_values(layout_lines, "classifier", "negative_keywords")),
        header_keywords=tuple(_nested_list_values(layout_lines, "table_detection", "header_keywords")),
        source_path=path.name,
    )


def _layout_profile_lines(lines: list[str]) -> list[str]:
    for index, line in enumerate(lines):
        if line.strip() == "layout_profile:":
            return lines[index + 1 :]
    return []


def _scalar_value(lines: list[str], key: str) -> str:
    pattern = re.compile(rf"^  {re.escape(key)}:\s*(.*)$")
    for line in lines:
        match = pattern.match(line)
        if match:
            return _clean_scalar(match.group(1))
    return ""


def _nested_scalar_value(lines: list[str], parent_key: str, child_key: str) -> str:
    parent_range = _section_range(lines, indent=2, key=parent_key)
    if parent_range is None:
        return ""

    start, end = parent_range
    pattern = re.compile(rf"^    {re.escape(child_key)}:\s*(.*)$")
    for line in lines[start:end]:
        match = pattern.match(line)
        if match:
            return _clean_scalar(match.group(1))
    return ""


def _nested_list_values(lines: list[str], parent_key: str, child_key: str) -> list[str]:
    parent_range = _section_range(lines, indent=2, key=parent_key)
    if parent_range is None:
        return []

    start, end = parent_range
    child_range = _section_range(lines[start:end], indent=4, key=child_key)
    if child_range is None:
        return []

    child_start, child_end = child_range
    values: list[str] = []
    for line in lines[start + child_start : start + child_end]:
        item = _list_item_value(line)
        if item:
            values.append(item)
    return values


def _section_range(lines: list[str], *, indent: int, key: str) -> tuple[int, int] | None:
    prefix = " " * indent
    start: int | None = None
    for index, line in enumerate(lines):
        if start is None:
            if line.startswith(prefix) and line.strip() == f"{key}:":
                start = index + 1
            continue

        if line.startswith(prefix) and not line.startswith(prefix + " ") and line.strip().endswith(":"):
            return start, index

    if start is None:
        return None
    return start, len(lines)


def _list_item_value(line: str) -> str:
    stripped = line.strip()
    if not stripped.startswith("- "):
        return ""
    return _clean_scalar(stripped[2:])


def _clean_scalar(raw: str) -> str:
    value = raw.strip()
    if not value or value in {"[]", "null", "unknown"}:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    return value.strip()


def _float_value(raw: str, *, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _keyword_hits(keywords: tuple[str, ...], normalized_text: str) -> tuple[int, float]:
    normalized_keywords = [normalize_upper_text(keyword) for keyword in keywords if keyword.strip()]
    if not normalized_keywords:
        return 0, 0.0

    hits = sum(1 for keyword in normalized_keywords if keyword and keyword in normalized_text)
    return hits, hits / len(normalized_keywords)
