from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

QUALITY_RULE_VERSION = "2026-09-10.v1"
PDF_LAYOUT_CONFIDENCE_THRESHOLD = 0.95


@dataclass(frozen=True)
class ConversionQualityAssessment:
    status: str
    score: float | None
    rule_version: str
    reason_codes: tuple[str, ...]


def assess_conversion_quality(
    *,
    status: str,
    conversion_type: str,
    transactions_count: int | None,
    layout_name: str | None = None,
    layout_confidence: float | None = None,
    selected_parser: str | None = None,
    warning_count: int = 0,
    balance_failed: int = 0,
    parser_confidence_band: str | None = None,
    parser_coverage_rate: float | None = None,
) -> ConversionQualityAssessment:
    del parser_confidence_band, parser_coverage_rate
    normalized_status = str(status or "").strip().casefold()
    if normalized_status in {"processando", "processing", "pending"}:
        return ConversionQualityAssessment("processing", None, QUALITY_RULE_VERSION, ())
    if normalized_status not in {"sucesso", "success", "completed"}:
        return ConversionQualityAssessment("failed", None, QUALITY_RULE_VERSION, ("technical_failure",))

    reasons: list[str] = []
    if max(0, int(transactions_count or 0)) == 0:
        reasons.append("no_transactions")
    if max(0, int(warning_count or 0)) > 0:
        reasons.append("row_warnings")
    if max(0, int(balance_failed or 0)) > 0:
        reasons.append("balance_inconsistency")

    is_pdf = str(conversion_type or "").strip().casefold().startswith("pdf")
    score: float | None = None
    if layout_confidence is not None:
        score = max(0.0, min(1.0, float(layout_confidence)))
    if is_pdf:
        normalized_layout = str(layout_name or "").strip().casefold()
        if not normalized_layout or normalized_layout.startswith("generic"):
            reasons.append("generic_layout")
        if score is None:
            reasons.append("layout_confidence_missing")
        elif score < PDF_LAYOUT_CONFIDENCE_THRESHOLD:
            reasons.append("layout_confidence_below_95")
        if not str(selected_parser or "").strip():
            reasons.append("parser_missing")

    return ConversionQualityAssessment(
        "clean" if not reasons else "review",
        score,
        QUALITY_RULE_VERSION,
        tuple(dict.fromkeys(reasons)),
    )


def build_line_quality_issues(rows: Iterable[object]) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for transaction_index, row in enumerate(rows):
        warnings = getattr(row, "warnings", None) or []
        for warning in dict.fromkeys(_safe_code(item) for item in warnings):
            if warning is None:
                continue
            issues.append(
                {
                    "scope": "transaction",
                    "severity": "error" if warning == "balance_consistency_failed" else "warning",
                    "issue_code": warning,
                    "transaction_index": transaction_index,
                    "source_page": _optional_non_negative_int(getattr(row, "source_page", None)),
                    "source_line": _optional_non_negative_int(getattr(row, "source_line", None)),
                    "source_parser": str(getattr(row, "source_parser", None) or "").strip() or None,
                }
            )
    return issues


def sanitize_failure_diagnostics(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed = {
        "pdf_read_ok",
        "text_extracted_likely",
        "missing_signals",
        "pdf_structure_read_ok",
        "native_text_extraction_ok",
        "native_text_error_type",
        "has_date_like",
        "has_amount_like",
        "inline_candidates",
        "tabular_candidates",
        "columnar_candidates",
        "multiline_candidates",
        "textract_attempted",
        "textract_used",
        "native_text_detected",
        "textract_error_type",
        "ocr_fallback_attempted",
        "ocr_max_pages",
    }
    return {key: value[key] for key in allowed if key in value and _is_safe_diagnostic_value(value[key])}


def _is_safe_diagnostic_value(value: object) -> bool:
    if value is None or isinstance(value, (bool, int, float)):
        return True
    if isinstance(value, str):
        return _safe_code(value) is not None
    if isinstance(value, list):
        return len(value) <= 20 and all(_safe_code(item) is not None for item in value)
    return False


def _safe_code(value: object) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 100 or re.fullmatch(r"[A-Za-z0-9_.:-]+", normalized) is None:
        return None
    return normalized


def _optional_non_negative_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None
