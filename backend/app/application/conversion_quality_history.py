from __future__ import annotations

import json
import re
from typing import Callable

from app.application.conversion_quality import assess_conversion_quality, sanitize_failure_diagnostics


def prepare_quality_record(
    *,
    status: str,
    conversion_type: str,
    transactions_count: int | None,
    layout_name: str | None,
    layout_confidence: float | None,
    selected_parser: str | None,
    warning_count: int,
    balance_failed: int,
    parser_confidence_band: str | None,
    parser_coverage_rate: float | None,
    warning_types: list[str] | None,
    failure_diagnostics: dict[str, object] | None,
) -> tuple[object, str, str, str]:
    assessment = assess_conversion_quality(
        status=status,
        conversion_type=conversion_type,
        transactions_count=transactions_count,
        layout_name=layout_name,
        layout_confidence=layout_confidence,
        selected_parser=selected_parser,
        warning_count=warning_count,
        balance_failed=balance_failed,
        parser_confidence_band=parser_confidence_band,
        parser_coverage_rate=parser_coverage_rate,
    )
    normalized_warnings = sorted({str(item).strip() for item in warning_types or [] if str(item).strip()})
    return (
        assessment,
        json.dumps(assessment.reason_codes, ensure_ascii=False),
        json.dumps(normalized_warnings, ensure_ascii=False),
        json.dumps(sanitize_failure_diagnostics(failure_diagnostics), ensure_ascii=False),
    )


def replace_quality_issues(
    conn,
    *,
    execute: Callable,
    conversion_id: str,
    identity_type: str,
    created_at: str,
    quality_reason_codes: tuple[str, ...],
    quality_issues: list[dict[str, object]] | None,
    error_code: str | None,
) -> None:
    execute(
        conn,
        "DELETE FROM conversion_quality_issues WHERE conversion_id = ? AND identity_type = ?",
        (conversion_id, identity_type),
    )
    items = list(quality_issues or [])
    existing_codes = {str(item.get("issue_code") or "") for item in items}
    for reason_code in quality_reason_codes:
        if reason_code not in existing_codes:
            items.append({"scope": "conversion", "severity": "warning", "issue_code": reason_code})
    if error_code and error_code not in existing_codes:
        items.append({"scope": "conversion", "severity": "error", "issue_code": error_code})

    for issue_index, item in enumerate(items):
        issue_code = _safe_code(item.get("issue_code"))
        if not issue_code:
            continue
        execute(
            conn,
            """
            INSERT INTO conversion_quality_issues (
              conversion_id, identity_type, issue_index, created_at, scope, severity,
              issue_code, transaction_index, source_page, source_line, source_parser, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conversion_id,
                identity_type,
                issue_index,
                created_at,
                _limited_text(item.get("scope"), "conversion", 32),
                _limited_text(item.get("severity"), "warning", 16),
                issue_code,
                _optional_int(item.get("transaction_index")),
                _optional_int(item.get("source_page")),
                _optional_int(item.get("source_line")),
                _limited_text(item.get("source_parser"), None, 64),
                None,
            ),
        )


def _limited_text(value: object, default: str | None, limit: int) -> str | None:
    normalized = str(value or "").strip()
    return normalized[:limit] if normalized else default


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _safe_code(value: object) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 100 or re.fullmatch(r"[A-Za-z0-9_.:-]+", normalized) is None:
        return None
    return normalized
