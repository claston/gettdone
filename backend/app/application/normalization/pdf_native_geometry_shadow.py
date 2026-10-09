from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from dataclasses import dataclass, replace
from time import monotonic
from typing import Callable, Mapping

from app.application.models import NormalizedTransaction
from app.application.normalization.pdf_native_geometry import (
    NativeGeometryMetrics,
    extract_native_pdf_geometry,
)
from app.application.normalization.text import normalize_description_text
from app.application.parsers.pdf.models import PdfParseResult


@dataclass(frozen=True, slots=True)
class NativeGeometryShadowObservation:
    classification: str
    baseline_status: str
    baseline_layout: str
    baseline_parser: str
    baseline_transactions: int
    baseline_balance_failed: int
    geometry_status: str
    geometry_layout: str
    geometry_parser: str
    geometry_transactions: int
    geometry_balance_failed: int
    duration_ms: int
    matched_transactions: int
    date_conflicts: int
    amount_conflicts: int
    sign_conflicts: int
    geometry_error_type: str
    word_count: int = 0
    line_count: int = 0
    duplicate_characters_removed: int = 0
    fragment_merges: int = 0

    def as_parse_metrics(self) -> dict[str, int | str]:
        return {
            "native_geometry_shadow_attempted": 1,
            "native_geometry_shadow_classification": self.classification,
            "native_geometry_shadow_baseline_status": self.baseline_status,
            "native_geometry_shadow_baseline_layout": self.baseline_layout,
            "native_geometry_shadow_baseline_parser": self.baseline_parser,
            "native_geometry_shadow_baseline_transactions": self.baseline_transactions,
            "native_geometry_shadow_baseline_balance_failed": self.baseline_balance_failed,
            "native_geometry_shadow_status": self.geometry_status,
            "native_geometry_shadow_layout": self.geometry_layout,
            "native_geometry_shadow_parser": self.geometry_parser,
            "native_geometry_shadow_transactions": self.geometry_transactions,
            "native_geometry_shadow_balance_failed": self.geometry_balance_failed,
            "native_geometry_shadow_duration_ms": self.duration_ms,
            "native_geometry_shadow_matched_transactions": self.matched_transactions,
            "native_geometry_shadow_date_conflicts": self.date_conflicts,
            "native_geometry_shadow_amount_conflicts": self.amount_conflicts,
            "native_geometry_shadow_sign_conflicts": self.sign_conflicts,
            "native_geometry_shadow_error_type": self.geometry_error_type,
            "native_geometry_shadow_word_count": self.word_count,
            "native_geometry_shadow_line_count": self.line_count,
            "native_geometry_shadow_duplicate_characters_removed": self.duplicate_characters_removed,
            "native_geometry_shadow_fragment_merges": self.fragment_merges,
        }


PageTextParser = Callable[..., PdfParseResult]


def should_run_native_geometry_shadow(
    raw_bytes: bytes,
    *,
    environment: Mapping[str, str] | None = None,
) -> bool:
    values = os.environ if environment is None else environment
    enabled = str(values.get("PDF_NATIVE_GEOMETRY_SHADOW_ENABLED") or "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    try:
        percent = int(str(values.get("PDF_NATIVE_GEOMETRY_SHADOW_PERCENT") or "0").strip())
    except ValueError:
        return False
    percent = max(0, min(percent, 100))
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    bucket = int.from_bytes(hashlib.sha256(raw_bytes).digest()[:4], byteorder="big") % 100
    return bucket < percent


def run_native_geometry_shadow(
    raw_bytes: bytes,
    *,
    baseline_result: PdfParseResult | None,
    baseline_error: Exception | None,
    parse_page_texts: PageTextParser,
) -> NativeGeometryShadowObservation:
    if _explicitly_lacks_native_text(baseline_result, baseline_error):
        return compare_native_geometry_results(
            baseline_result=baseline_result,
            baseline_error=baseline_error,
            geometry_result=None,
            geometry_error=None,
            duration_ms=0,
            geometry_status="skipped_no_native_text",
        )

    started_at = monotonic()
    extraction_metrics: NativeGeometryMetrics | None = None
    geometry_result: PdfParseResult | None = None
    geometry_error: Exception | None = None
    geometry_status = "error"
    try:
        extraction = extract_native_pdf_geometry(raw_bytes)
        extraction_metrics = extraction.metrics
        if extraction.metrics.word_count <= 0 or not any(page.strip() for page in extraction.page_texts):
            geometry_status = "no_text"
        else:
            geometry_result = parse_page_texts(
                list(extraction.page_texts),
                preserve_layout_spacing=True,
            )
            geometry_status = "ok"
    except Exception as exc:  # shadow failures must never affect the official result
        geometry_error = exc
        geometry_status = "error"

    duration_ms = max(0, round((monotonic() - started_at) * 1000))
    observation = compare_native_geometry_results(
        baseline_result=baseline_result,
        baseline_error=baseline_error,
        geometry_result=geometry_result,
        geometry_error=geometry_error,
        duration_ms=duration_ms,
        geometry_status=geometry_status,
    )
    if extraction_metrics is None:
        return observation
    return replace(
        observation,
        word_count=extraction_metrics.word_count,
        line_count=extraction_metrics.line_count,
        duplicate_characters_removed=extraction_metrics.duplicate_characters_removed,
        fragment_merges=extraction_metrics.fragment_merges,
    )


def compare_native_geometry_results(
    *,
    baseline_result: PdfParseResult | None,
    baseline_error: Exception | None,
    geometry_result: PdfParseResult | None,
    geometry_error: Exception | None,
    duration_ms: int,
    geometry_status: str | None = None,
) -> NativeGeometryShadowObservation:
    baseline_transactions = list(baseline_result.transactions) if baseline_result is not None else []
    geometry_transactions = list(geometry_result.transactions) if geometry_result is not None else []
    matched, unmatched_baseline, unmatched_geometry = _match_exact_transactions(
        baseline_transactions,
        geometry_transactions,
    )
    date_conflicts, amount_conflicts, sign_conflicts = _count_unambiguous_conflicts(
        unmatched_baseline,
        unmatched_geometry,
    )
    baseline_failed = baseline_result is None
    geometry_failed = geometry_result is None
    resolved_geometry_status = geometry_status or ("error" if geometry_failed else "ok")
    baseline_balance_failed = _metric_int(baseline_result, "balance_consistency_failed")
    geometry_balance_failed = _metric_int(geometry_result, "balance_consistency_failed")

    if resolved_geometry_status in {"no_text", "skipped_no_native_text"}:
        classification = "not_applicable"
    elif baseline_failed and not geometry_failed and geometry_transactions:
        classification = "potential_rescue"
    elif baseline_failed and geometry_failed:
        classification = "shadow_error"
    elif not baseline_failed and geometry_failed:
        classification = "regression"
    elif date_conflicts or amount_conflicts or sign_conflicts:
        classification = "conflict"
    elif matched == len(baseline_transactions) == len(geometry_transactions):
        classification = "equivalent"
    elif (
        len(geometry_transactions) > len(baseline_transactions)
        and matched == len(baseline_transactions)
        and geometry_balance_failed <= baseline_balance_failed
    ):
        classification = "potential_gain"
    elif len(geometry_transactions) < len(baseline_transactions):
        classification = "regression"
    else:
        classification = "inconclusive"

    return NativeGeometryShadowObservation(
        classification=classification,
        baseline_status="error" if baseline_failed else "ok",
        baseline_layout=_layout_name(baseline_result),
        baseline_parser=_parser_name(baseline_result),
        baseline_transactions=len(baseline_transactions),
        baseline_balance_failed=baseline_balance_failed,
        geometry_status=resolved_geometry_status,
        geometry_layout=_layout_name(geometry_result),
        geometry_parser=_parser_name(geometry_result),
        geometry_transactions=len(geometry_transactions),
        geometry_balance_failed=geometry_balance_failed,
        duration_ms=max(0, int(duration_ms)),
        matched_transactions=matched,
        date_conflicts=date_conflicts,
        amount_conflicts=amount_conflicts,
        sign_conflicts=sign_conflicts,
        geometry_error_type=geometry_error.__class__.__name__ if geometry_error is not None else "",
    )


def _match_exact_transactions(
    baseline: list[NormalizedTransaction],
    geometry: list[NormalizedTransaction],
) -> tuple[int, list[NormalizedTransaction], list[NormalizedTransaction]]:
    available = set(range(len(geometry)))
    unmatched_baseline: list[NormalizedTransaction] = []
    matched = 0
    for transaction in baseline:
        signature = _transaction_signature(transaction)
        match_index = next(
            (index for index in sorted(available) if _transaction_signature(geometry[index]) == signature),
            None,
        )
        if match_index is None:
            unmatched_baseline.append(transaction)
            continue
        available.remove(match_index)
        matched += 1
    return matched, unmatched_baseline, [geometry[index] for index in sorted(available)]


def _count_unambiguous_conflicts(
    baseline: list[NormalizedTransaction],
    geometry: list[NormalizedTransaction],
) -> tuple[int, int, int]:
    baseline_by_description: dict[str, list[NormalizedTransaction]] = defaultdict(list)
    geometry_by_description: dict[str, list[NormalizedTransaction]] = defaultdict(list)
    for transaction in baseline:
        baseline_by_description[_description_key(transaction)].append(transaction)
    for transaction in geometry:
        geometry_by_description[_description_key(transaction)].append(transaction)

    date_conflicts = 0
    amount_conflicts = 0
    sign_conflicts = 0
    for description in baseline_by_description.keys() & geometry_by_description.keys():
        baseline_items = baseline_by_description[description]
        geometry_items = geometry_by_description[description]
        if len(baseline_items) != 1 or len(geometry_items) != 1:
            continue
        baseline_item = baseline_items[0]
        geometry_item = geometry_items[0]
        if baseline_item.date != geometry_item.date:
            date_conflicts += 1
        if abs(float(baseline_item.amount) - float(geometry_item.amount)) > 0.005:
            amount_conflicts += 1
        if _amount_sign(baseline_item.amount) != _amount_sign(geometry_item.amount):
            sign_conflicts += 1
    return date_conflicts, amount_conflicts, sign_conflicts


def _transaction_signature(transaction: NormalizedTransaction) -> tuple[str, str, float, str]:
    return (
        str(transaction.date),
        _description_key(transaction),
        round(float(transaction.amount), 2),
        str(transaction.type),
    )


def _description_key(transaction: NormalizedTransaction) -> str:
    return normalize_description_text(transaction.description)


def _amount_sign(amount: float) -> int:
    value = float(amount)
    return 1 if value > 0 else -1 if value < 0 else 0


def _metric_int(result: PdfParseResult | None, key: str) -> int:
    if result is None:
        return 0
    return max(0, int(result.parse_metrics.get(key, 0) or 0))


def _explicitly_lacks_native_text(
    baseline_result: PdfParseResult | None,
    baseline_error: Exception | None,
) -> bool:
    if baseline_result is not None:
        value = baseline_result.parse_metrics.get("native_text_detected")
    elif baseline_error is not None:
        value = dict(getattr(baseline_error, "_parse_observability", {}) or {}).get(
            "native_text_detected"
        )
    else:
        value = None
    if value is None:
        return False
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return False


def _layout_name(result: PdfParseResult | None) -> str:
    return str(result.layout.layout_name) if result is not None else ""


def _parser_name(result: PdfParseResult | None) -> str:
    return str(result.parse_metrics.get("selected_parser", "")) if result is not None else ""
