from __future__ import annotations

from typing import Callable

SUPPORTED_IDENTITY_TYPES = frozenset({"registered", "anonymous"})


def record_pdf_native_geometry_shadow_event(
    conn,
    *,
    execute: Callable,
    now_iso: str,
    processing_id: str,
    identity_type: str,
    classification: str,
    baseline_status: str,
    baseline_layout: str | None,
    baseline_parser: str | None,
    baseline_transactions: int,
    baseline_balance_failed: int,
    geometry_status: str,
    geometry_layout: str | None,
    geometry_parser: str | None,
    geometry_transactions: int,
    geometry_balance_failed: int,
    geometry_duration_ms: int,
    matched_transactions: int,
    date_conflicts: int,
    amount_conflicts: int,
    sign_conflicts: int,
    geometry_error_type: str | None,
    word_count: int,
    line_count: int,
    duplicate_characters_removed: int,
    fragment_merges: int,
    created_at: str | None = None,
) -> None:
    normalized_identity_type = str(identity_type or "").strip().lower()
    if normalized_identity_type not in SUPPORTED_IDENTITY_TYPES:
        raise ValueError("Unsupported identity_type")

    values = (
        _required_text(processing_id, 160),
        normalized_identity_type,
        created_at or now_iso,
        _required_text(classification, 80),
        _required_text(baseline_status, 40),
        _optional_text(baseline_layout, 160),
        _optional_text(baseline_parser, 120),
        _non_negative_int(baseline_transactions),
        _non_negative_int(baseline_balance_failed),
        _required_text(geometry_status, 40),
        _optional_text(geometry_layout, 160),
        _optional_text(geometry_parser, 120),
        _non_negative_int(geometry_transactions),
        _non_negative_int(geometry_balance_failed),
        _non_negative_int(geometry_duration_ms),
        _non_negative_int(matched_transactions),
        _non_negative_int(date_conflicts),
        _non_negative_int(amount_conflicts),
        _non_negative_int(sign_conflicts),
        _optional_text(geometry_error_type, 160),
        _non_negative_int(word_count),
        _non_negative_int(line_count),
        _non_negative_int(duplicate_characters_removed),
        _non_negative_int(fragment_merges),
    )
    execute(
        conn,
        """
        INSERT INTO pdf_native_geometry_shadow_events (
            processing_id, identity_type, created_at, classification,
            baseline_status, baseline_layout, baseline_parser,
            baseline_transactions, baseline_balance_failed,
            geometry_status, geometry_layout, geometry_parser,
            geometry_transactions, geometry_balance_failed, geometry_duration_ms,
            matched_transactions, date_conflicts, amount_conflicts, sign_conflicts,
            geometry_error_type, word_count, line_count,
            duplicate_characters_removed, fragment_merges
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(processing_id, identity_type)
        DO UPDATE SET
            created_at=excluded.created_at,
            classification=excluded.classification,
            baseline_status=excluded.baseline_status,
            baseline_layout=excluded.baseline_layout,
            baseline_parser=excluded.baseline_parser,
            baseline_transactions=excluded.baseline_transactions,
            baseline_balance_failed=excluded.baseline_balance_failed,
            geometry_status=excluded.geometry_status,
            geometry_layout=excluded.geometry_layout,
            geometry_parser=excluded.geometry_parser,
            geometry_transactions=excluded.geometry_transactions,
            geometry_balance_failed=excluded.geometry_balance_failed,
            geometry_duration_ms=excluded.geometry_duration_ms,
            matched_transactions=excluded.matched_transactions,
            date_conflicts=excluded.date_conflicts,
            amount_conflicts=excluded.amount_conflicts,
            sign_conflicts=excluded.sign_conflicts,
            geometry_error_type=excluded.geometry_error_type,
            word_count=excluded.word_count,
            line_count=excluded.line_count,
            duplicate_characters_removed=excluded.duplicate_characters_removed,
            fragment_merges=excluded.fragment_merges
        """,
        values,
    )


def _required_text(value: str, max_length: int) -> str:
    normalized = str(value or "").strip()[:max_length]
    if not normalized:
        raise ValueError("Required shadow telemetry value is empty")
    return normalized


def _optional_text(value: str | None, max_length: int) -> str | None:
    normalized = str(value or "").strip()[:max_length]
    return normalized or None


def _non_negative_int(value: int) -> int:
    return max(0, int(value or 0))
