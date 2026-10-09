from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from statistics import median
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from app.application.conversion_quality import assess_conversion_quality

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


DASHBOARD_TIMEZONE_NAME = "America/Sao_Paulo"
DASHBOARD_TIMEZONE = ZoneInfo(DASHBOARD_TIMEZONE_NAME)
SUPPORTED_IDENTITY_TYPES = frozenset({"all", "registered", "anonymous"})
CANONICAL_CAPTURE_STATUS_ORDER = (
    "stored",
    "upload_failed",
    "generation_failed",
    "boundary_failed",
    "skipped_privacy",
    "skipped_unsupported",
    "not_eligible",
    "not_attempted",
    "disabled",
    "not_recorded",
)
CANONICAL_CAPTURE_CANDIDATE_STATUSES = frozenset(
    {
        "stored",
        "upload_failed",
        "generation_failed",
        "boundary_failed",
        "skipped_privacy",
        "skipped_unsupported",
    }
)
CANONICAL_CAPTURE_FAILURE_STATUSES = frozenset(
    {"upload_failed", "generation_failed", "boundary_failed"}
)
CANONICAL_CAPTURE_SKIPPED_STATUSES = frozenset({"skipped_privacy", "skipped_unsupported"})
NON_CONVERSION_ERROR_CODES = frozenset(
    {
        "file_too_large",
        "monthly_pages_quota_exceeded",
        "pages_limit_exceeded",
        "quota_exceeded",
        "weekly_quota_exceeded",
    }
)


class AdminDashboardService:
    """Read-only admin metrics built from conversion history persisted by the web app."""

    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def get_dashboard(self, *, days: int = 30, identity_type: str = "all") -> dict[str, object]:
        normalized_days = max(1, min(int(days), 90))
        normalized_identity_type = str(identity_type or "all").strip().lower()
        if normalized_identity_type not in SUPPORTED_IDENTITY_TYPES:
            raise ValueError("Unsupported identity_type")

        now_utc = _as_aware_utc(self._service.now_provider())
        now_local = now_utc.astimezone(DASHBOARD_TIMEZONE)
        start_date = now_local.date() - timedelta(days=normalized_days - 1)
        start_local = datetime.combine(start_date, time.min, tzinfo=DASHBOARD_TIMEZONE)
        start_utc = start_local.astimezone(timezone.utc)

        with self._service._lock:
            with self._service._connect() as conn:
                events = self._load_period_events(
                    conn,
                    start_at=start_utc.isoformat(),
                    end_at=now_utc.isoformat(),
                    identity_type=normalized_identity_type,
                )
                prior_identity_keys = self._load_prior_identity_keys(
                    conn,
                    before=start_utc.isoformat(),
                    identity_type=normalized_identity_type,
                )
                top_quality_issues = self._load_top_quality_issues(
                    conn,
                    start_at=start_utc.isoformat(),
                    end_at=now_utc.isoformat(),
                    identity_type=normalized_identity_type,
                )
                product_events = self._load_product_events(
                    conn,
                    start_at=start_utc.isoformat(),
                    end_at=now_utc.isoformat(),
                    identity_type=normalized_identity_type,
                )
                native_geometry_events = self._load_native_geometry_shadow_events(
                    conn,
                    start_at=start_utc.isoformat(),
                    end_at=now_utc.isoformat(),
                    identity_type=normalized_identity_type,
                )
                registered_profiles = self._load_registered_profiles(
                    conn,
                    events=events,
                    extra_registered_ids=_registered_product_identity_ids(product_events),
                )
                checkout_intents = (
                    self._load_checkout_intents(
                        conn,
                        start_at=start_utc.isoformat(),
                        end_at=now_utc.isoformat(),
                    )
                    if normalized_identity_type in {"all", "registered"}
                    else []
                )

        return _build_dashboard_payload(
            events=events,
            prior_identity_keys=prior_identity_keys,
            days=normalized_days,
            start_date=start_date,
            start_at=start_utc,
            end_at=now_utc,
            top_quality_issues=top_quality_issues,
            registered_profiles=registered_profiles,
            checkout_intents=checkout_intents,
            product_events=product_events,
            native_geometry_events=native_geometry_events,
        )

    def get_attention_export_rows(
        self,
        *,
        days: int = 7,
        identity_type: str = "all",
    ) -> list[dict[str, object]]:
        normalized_days = max(1, min(int(days), 90))
        normalized_identity_type = str(identity_type or "all").strip().lower()
        if normalized_identity_type not in SUPPORTED_IDENTITY_TYPES:
            raise ValueError("Unsupported identity_type")

        now_utc = _as_aware_utc(self._service.now_provider())
        now_local = now_utc.astimezone(DASHBOARD_TIMEZONE)
        start_date = now_local.date() - timedelta(days=normalized_days - 1)
        start_local = datetime.combine(start_date, time.min, tzinfo=DASHBOARD_TIMEZONE)
        start_utc = start_local.astimezone(timezone.utc)

        with self._service._lock:
            with self._service._connect() as conn:
                events = self._load_period_events(
                    conn,
                    start_at=start_utc.isoformat(),
                    end_at=now_utc.isoformat(),
                    identity_type=normalized_identity_type,
                )

        export_rows: list[dict[str, object]] = []
        for event in events:
            if _is_non_conversion_event(event):
                continue
            is_success = _is_success_status(str(event["status"]))
            is_clean = is_success and str(event["quality_status"]) == "clean"
            if is_clean:
                continue
            attention = _attention_item(event, is_success=is_success)
            created_at = _parse_datetime(str(event["created_at"]))
            export_rows.append(
                {
                    "created_at": created_at.astimezone(DASHBOARD_TIMEZONE).isoformat()
                    if created_at
                    else str(event["created_at"]),
                    "processing_id": str(event["processing_id"]),
                    "identity_type": str(event["identity_type"]),
                    "model": str(event["model"]),
                    "conversion_type": str(event["conversion_type"]),
                    "status": str(event["status"]),
                    "pages_count": int(event["pages_count"]),
                    "ocr_used": bool(event["ocr_used"]),
                    "transactions_count": int(event["transactions_count"]),
                    "layout_name": event.get("layout_name"),
                    "layout_confidence": event.get("layout_confidence"),
                    "selected_parser": event.get("selected_parser"),
                    "error_code": event.get("error_code"),
                    "error_stage": event.get("error_stage"),
                    "quality_status": event.get("quality_status"),
                    "quality_reason_codes": event.get("quality_reason_codes", []),
                    "issue_reason": attention["issue_reason"],
                    "canonical_capture_status": event.get("canonical_capture_status"),
                    "canonical_capture_reason": event.get("canonical_capture_reason"),
                }
            )

        export_rows.sort(key=lambda item: str(item["created_at"]), reverse=True)
        return export_rows

    def get_active_users(
        self,
        *,
        days: int = 30,
        identity_type: str = "all",
        prospect_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, object]:
        normalized_days = max(1, min(int(days), 90))
        normalized_identity_type = str(identity_type or "all").strip().lower()
        if normalized_identity_type not in SUPPORTED_IDENTITY_TYPES:
            raise ValueError("Unsupported identity_type")
        normalized_limit = max(1, min(int(limit), 200))
        normalized_offset = max(0, int(offset))

        now_utc = _as_aware_utc(self._service.now_provider())
        now_local = now_utc.astimezone(DASHBOARD_TIMEZONE)
        start_date = now_local.date() - timedelta(days=normalized_days - 1)
        start_utc = datetime.combine(start_date, time.min, tzinfo=DASHBOARD_TIMEZONE).astimezone(timezone.utc)

        with self._service._lock:
            with self._service._connect() as conn:
                conversion_events = self._load_period_events(
                    conn,
                    start_at=start_utc.isoformat(),
                    end_at=now_utc.isoformat(),
                    identity_type=normalized_identity_type,
                )
                prior_identity_keys = self._load_prior_identity_keys(
                    conn,
                    before=start_utc.isoformat(),
                    identity_type=normalized_identity_type,
                )
                product_events = self._load_product_events(
                    conn,
                    start_at=start_utc.isoformat(),
                    end_at=now_utc.isoformat(),
                    identity_type=normalized_identity_type,
                )
                registered_profiles = self._load_registered_profiles(
                    conn,
                    events=conversion_events,
                    extra_registered_ids=_registered_product_identity_ids(product_events),
                )

        conversion_events = [
            event for event in conversion_events if not _is_non_conversion_event(event)
        ]
        items = _build_user_usage(
            conversion_events,
            prior_identity_keys=prior_identity_keys,
            registered_profiles=registered_profiles,
        )
        items = _enrich_active_users(
            items,
            product_events=product_events,
            registered_profiles=registered_profiles,
        )
        if prospect_only:
            items = [item for item in items if item["plan_code"] == "free"]
        items.sort(
            key=lambda item: (
                -int(item["purchase_score"]),
                -int(item["pages"]),
                -int(item["conversions"]),
                str(item["identity_reference"]),
            )
        )
        summary_items = items
        page_items = items[normalized_offset : normalized_offset + normalized_limit]
        return {
            "days": normalized_days,
            "start_at": start_utc.isoformat(),
            "end_at": now_utc.isoformat(),
            "timezone": DASHBOARD_TIMEZONE_NAME,
            "summary": _build_active_users_summary(summary_items),
            "items": page_items,
            "total": len(items),
            "limit": normalized_limit,
            "offset": normalized_offset,
        }

    def _load_top_quality_issues(self, conn, *, start_at: str, end_at: str, identity_type: str) -> list[dict[str, object]]:
        placeholders = ", ".join("?" for _ in NON_CONVERSION_ERROR_CODES)
        excluded_codes = tuple(sorted(NON_CONVERSION_ERROR_CODES))
        sql = f"""
            SELECT issues.issue_code, issues.severity, COUNT(*) AS issue_count
            FROM conversion_quality_issues AS issues
            LEFT JOIN user_conversions AS registered
              ON issues.identity_type = 'registered'
             AND issues.conversion_id = registered.analysis_id
            LEFT JOIN anonymous_conversion_events AS anonymous
              ON issues.identity_type = 'anonymous'
             AND issues.conversion_id = anonymous.id
            WHERE issues.created_at >= ? AND issues.created_at <= ?
              AND COALESCE(registered.error_code, anonymous.error_code, '') NOT IN ({placeholders})
        """
        params: tuple[object, ...] = (start_at, end_at, *excluded_codes)
        if identity_type != "all":
            sql += " AND issues.identity_type = ?"
            params += (identity_type,)
        sql += (
            " GROUP BY issues.issue_code, issues.severity"
            " ORDER BY issue_count DESC, issues.issue_code ASC LIMIT 10"
        )
        rows = self._service._fetchall(conn, sql, params)
        return [
            {
                "issue_code": str(row["issue_code"]),
                "severity": str(row["severity"]),
                "count": int(row["issue_count"]),
            }
            for row in rows
        ]

    def _load_period_events(
        self,
        conn,
        *,
        start_at: str,
        end_at: str,
        identity_type: str,
    ) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        if identity_type in {"all", "registered"}:
            rows = self._service._fetchall(
                conn,
                """
                SELECT
                    analysis_id AS processing_id,
                    user_id AS identity_id,
                    created_at,
                    model,
                    conversion_type,
                    status,
                    transactions_count,
                    pages_count,
                    ocr_used,
                    duration_ms,
                    error_code,
                    error_stage,
                    canonical_warning_transactions_count,
                    balance_consistency_failed,
                    layout_inference_name,
                    layout_inference_confidence,
                    selected_parser,
                    quality_status,
                    quality_score,
                    quality_rule_version,
                    quality_reason_codes_json,
                    canonical_capture_status,
                    canonical_capture_reason
                FROM user_conversions
                WHERE created_at >= ? AND created_at <= ?
                """,
                (start_at, end_at),
            )
            events.extend(_row_to_event(row, identity_type="registered") for row in rows)

        if identity_type in {"all", "anonymous"}:
            rows = self._service._fetchall(
                conn,
                """
                SELECT
                    id AS processing_id,
                    anonymous_fingerprint AS identity_id,
                    created_at,
                    model,
                    conversion_type,
                    status,
                    transactions_count,
                    pages_count,
                    ocr_used,
                    duration_ms,
                    error_code,
                    error_stage,
                    canonical_warning_transactions_count,
                    balance_consistency_failed,
                    layout_inference_name,
                    layout_inference_confidence,
                    selected_parser,
                    quality_status,
                    quality_score,
                    quality_rule_version,
                    quality_reason_codes_json,
                    canonical_capture_status,
                    canonical_capture_reason
                FROM anonymous_conversion_events
                WHERE created_at >= ? AND created_at <= ?
                """,
                (start_at, end_at),
            )
            events.extend(_row_to_event(row, identity_type="anonymous") for row in rows)

        return events

    def _load_native_geometry_shadow_events(
        self,
        conn,
        *,
        start_at: str,
        end_at: str,
        identity_type: str,
    ) -> list[dict[str, object]]:
        sql = """
            SELECT
                processing_id, identity_type, created_at, classification,
                baseline_status, baseline_layout, baseline_parser,
                baseline_transactions, baseline_balance_failed,
                geometry_status, geometry_layout, geometry_parser,
                geometry_transactions, geometry_balance_failed, geometry_duration_ms,
                matched_transactions, date_conflicts, amount_conflicts, sign_conflicts,
                geometry_error_type, word_count, line_count,
                duplicate_characters_removed, fragment_merges
            FROM pdf_native_geometry_shadow_events
            WHERE created_at >= ? AND created_at <= ?
        """
        params: tuple[object, ...] = (start_at, end_at)
        if identity_type != "all":
            sql += " AND identity_type = ?"
            params += (identity_type,)
        sql += " ORDER BY created_at DESC"
        rows = self._service._fetchall(conn, sql, params)
        return [dict(row) for row in rows]

    def _load_prior_identity_keys(
        self,
        conn,
        *,
        before: str,
        identity_type: str,
    ) -> set[str]:
        identity_keys: set[str] = set()
        if identity_type in {"all", "registered"}:
            placeholders = ", ".join("?" for _ in NON_CONVERSION_ERROR_CODES)
            rows = self._service._fetchall(
                conn,
                f"""
                SELECT DISTINCT user_id AS identity_id
                FROM user_conversions
                WHERE created_at < ?
                  AND COALESCE(error_code, '') NOT IN ({placeholders})
                """,
                (before, *sorted(NON_CONVERSION_ERROR_CODES)),
            )
            identity_keys.update(f"registered:{row['identity_id']}" for row in rows)

        if identity_type in {"all", "anonymous"}:
            placeholders = ", ".join("?" for _ in NON_CONVERSION_ERROR_CODES)
            rows = self._service._fetchall(
                conn,
                f"""
                SELECT DISTINCT anonymous_fingerprint AS identity_id
                FROM anonymous_conversion_events
                WHERE created_at < ?
                  AND COALESCE(error_code, '') NOT IN ({placeholders})
                """,
                (before, *sorted(NON_CONVERSION_ERROR_CODES)),
            )
            identity_keys.update(f"anonymous:{row['identity_id']}" for row in rows)
        return identity_keys

    def _load_registered_profiles(
        self,
        conn,
        *,
        events: list[dict[str, object]],
        extra_registered_ids: set[str] | None = None,
    ) -> dict[str, dict[str, object]]:
        registered_ids = {
            str(event["identity_id"])
            for event in events
            if event["identity_type"] == "registered" and str(event["identity_id"])
        }
        registered_ids.update(extra_registered_ids or set())
        user_ids = sorted(registered_ids)
        if not user_ids:
            return {}
        placeholders = ", ".join("?" for _ in user_ids)
        rows = self._service._fetchall(
            conn,
            f"""
            SELECT
                users.id,
                users.name,
                users.email,
                users.product_updates_opt_in,
                COALESCE((
                    SELECT plan_versions.code
                    FROM user_plan_subscriptions
                    JOIN plan_versions ON plan_versions.id = user_plan_subscriptions.plan_version_id
                    WHERE user_plan_subscriptions.user_id = users.id
                      AND user_plan_subscriptions.status = 'active'
                    ORDER BY user_plan_subscriptions.started_at DESC
                    LIMIT 1
                ), 'free') AS plan_code
            FROM users
            WHERE users.id IN ({placeholders})
            """,
            tuple(user_ids),
        )
        return {
            str(row["id"]): {
                "name": str(row["name"] or ""),
                "email": str(row["email"] or ""),
                "marketing_contact_allowed": _as_bool(row["product_updates_opt_in"]),
                "plan_code": str(row["plan_code"] or "free").strip().lower() or "free",
            }
            for row in rows
        }

    def _load_product_events(
        self,
        conn,
        *,
        start_at: str,
        end_at: str,
        identity_type: str,
    ) -> list[dict[str, str]]:
        sql = """
            SELECT event_type, identity_type, identity_id, created_at,
                   page_path, plan_code, processing_id, download_format
            FROM product_events
            WHERE created_at >= ? AND created_at <= ?
        """
        params: tuple[object, ...] = (start_at, end_at)
        if identity_type != "all":
            sql += " AND identity_type = ?"
            params += (identity_type,)
        rows = self._service._fetchall(conn, sql, params)
        return [
            {
                "event_type": str(row["event_type"] or ""),
                "identity_type": str(row["identity_type"] or ""),
                "identity_id": str(row["identity_id"] or ""),
                "created_at": str(row["created_at"] or ""),
                "page_path": str(row["page_path"] or ""),
                "plan_code": str(row["plan_code"] or ""),
                "processing_id": str(row["processing_id"] or ""),
                "download_format": str(row["download_format"] or ""),
            }
            for row in rows
        ]

    def _load_checkout_intents(
        self,
        conn,
        *,
        start_at: str,
        end_at: str,
    ) -> list[dict[str, str]]:
        rows = self._service._fetchall(
            conn,
            """
            SELECT id, user_id, customer_email, status
            FROM checkout_intents
            WHERE created_at >= ? AND created_at <= ?
            """,
            (start_at, end_at),
        )
        return [
            {
                "id": str(row["id"] or ""),
                "user_id": str(row["user_id"] or ""),
                "customer_email": str(row["customer_email"] or "").strip().lower(),
                "status": str(row["status"] or "").strip().upper(),
            }
            for row in rows
        ]


def _row_to_event(row, *, identity_type: str) -> dict[str, object]:
    identity_id = str(row["identity_id"] or "")
    assessment = assess_conversion_quality(
        status=str(row["status"] or ""),
        conversion_type=str(row["conversion_type"] or ""),
        transactions_count=_as_non_negative_int(row["transactions_count"]),
        layout_name=str(row["layout_inference_name"] or "") or None,
        layout_confidence=_as_optional_float(row["layout_inference_confidence"]),
        selected_parser=str(row["selected_parser"] or "") or None,
        warning_count=_as_non_negative_int(row["canonical_warning_transactions_count"]),
        balance_failed=_as_non_negative_int(row["balance_consistency_failed"]),
    )
    stored_quality_status = str(row["quality_status"] or "").strip()
    stored_reasons = _json_string_list(row["quality_reason_codes_json"])
    return {
        "processing_id": str(row["processing_id"] or ""),
        "identity_type": identity_type,
        "identity_id": identity_id,
        "identity_key": f"{identity_type}:{identity_id}",
        "created_at": str(row["created_at"] or ""),
        "model": str(row["model"] or "Não identificado"),
        "conversion_type": str(row["conversion_type"] or "Não identificado"),
        "status": str(row["status"] or ""),
        "transactions_count": _as_non_negative_int(row["transactions_count"]),
        "pages_count": _as_non_negative_int(row["pages_count"]),
        "ocr_used": _as_bool(row["ocr_used"]),
        "duration_ms": _as_non_negative_int(row["duration_ms"]),
        "error_code": str(row["error_code"] or "").strip() or None,
        "error_stage": str(row["error_stage"] or "").strip() or None,
        "warning_count": _as_non_negative_int(row["canonical_warning_transactions_count"]),
        "balance_failed": _as_non_negative_int(row["balance_consistency_failed"]),
        "layout_name": str(row["layout_inference_name"] or "").strip() or None,
        "layout_confidence": _as_optional_float(row["layout_inference_confidence"]),
        "selected_parser": str(row["selected_parser"] or "").strip() or None,
        "quality_status": stored_quality_status or assessment.status,
        "quality_score": _as_optional_float(row["quality_score"])
        if row["quality_score"] is not None
        else assessment.score,
        "quality_rule_version": str(row["quality_rule_version"] or assessment.rule_version),
        "quality_reason_codes": stored_reasons or list(assessment.reason_codes),
        "canonical_capture_status": str(row["canonical_capture_status"] or "").strip() or "not_recorded",
        "canonical_capture_reason": str(row["canonical_capture_reason"] or "").strip() or None,
    }


def _build_dashboard_payload(
    *,
    events: list[dict[str, object]],
    prior_identity_keys: set[str],
    days: int,
    start_date: date,
    start_at: datetime,
    end_at: datetime,
    top_quality_issues: list[dict[str, object]],
    registered_profiles: dict[str, dict[str, object]],
    checkout_intents: list[dict[str, str]],
    product_events: list[dict[str, str]],
    native_geometry_events: list[dict[str, object]],
) -> dict[str, object]:
    non_conversion_count = sum(1 for event in events if _is_non_conversion_event(event))
    events = [event for event in events if not _is_non_conversion_event(event)]
    daily_by_date = {
        (start_date + timedelta(days=offset)).isoformat(): {
            "date": (start_date + timedelta(days=offset)).isoformat(),
            "conversions": 0,
            "clean": 0,
            "review": 0,
            "failures": 0,
        }
        for offset in range(days)
    }
    identity_dates: dict[str, set[str]] = defaultdict(set)
    identity_keys_by_type: dict[str, set[str]] = {
        "registered": set(),
        "anonymous": set(),
    }
    conversion_counts_by_type = Counter({"registered": 0, "anonymous": 0})
    top_error_counts: Counter[tuple[str, str]] = Counter()
    durations: list[int] = []
    recent_attention: list[dict[str, object]] = []
    success_count = 0
    clean_count = 0
    review_count = 0
    pages_total = 0
    pdf_conversions_count = 0
    pdf_pages_count = 0
    ocr_conversions_count = 0
    ocr_pages_count = 0
    layouts: dict[str, dict[str, object]] = {}

    for event in events:
        identity_key = str(event["identity_key"])
        event_identity_type = str(event["identity_type"])
        identity_keys_by_type[event_identity_type].add(identity_key)
        conversion_counts_by_type[event_identity_type] += 1
        pages_count = int(event["pages_count"])
        pages_total += pages_count
        if bool(event["ocr_used"]):
            ocr_conversions_count += 1
            ocr_pages_count += pages_count
        else:
            pdf_conversions_count += 1
            pdf_pages_count += pages_count

        created_at = _parse_datetime(str(event["created_at"]))
        local_date = created_at.astimezone(DASHBOARD_TIMEZONE).date().isoformat() if created_at else None
        if local_date:
            identity_dates[identity_key].add(local_date)

        is_success = _is_success_status(str(event["status"]))
        is_clean = is_success and str(event["quality_status"]) == "clean"
        if is_success:
            success_count += 1
            duration_ms = int(event["duration_ms"])
            if duration_ms > 0:
                durations.append(duration_ms)
        if is_clean:
            clean_count += 1
        elif is_success:
            review_count += 1

        layout_name = str(event.get("layout_name") or "Não identificado")
        layout = layouts.setdefault(
            layout_name,
            {
                "layout_name": layout_name,
                "conversions": 0,
                "successes": 0,
                "clean_high_confidence": 0,
                "review": 0,
                "failures": 0,
                "confidence_sum": 0.0,
                "confidence_count": 0,
            },
        )
        layout["conversions"] = int(layout["conversions"]) + 1
        if is_success:
            layout["successes"] = int(layout["successes"]) + 1
            key = "clean_high_confidence" if is_clean else "review"
            layout[key] = int(layout[key]) + 1
        else:
            layout["failures"] = int(layout["failures"]) + 1
        if event.get("layout_confidence") is not None:
            layout["confidence_sum"] = float(layout["confidence_sum"]) + float(event["layout_confidence"])
            layout["confidence_count"] = int(layout["confidence_count"]) + 1

        daily_item = daily_by_date.get(local_date or "")
        if daily_item is not None:
            daily_item["conversions"] += 1
            if not is_success:
                daily_item["failures"] += 1
            elif is_clean:
                daily_item["clean"] += 1
            else:
                daily_item["review"] += 1

        if not is_success:
            error_code = str(event["error_code"] or "unknown")
            error_stage = str(event["error_stage"] or "unknown")
            top_error_counts[(error_code, error_stage)] += 1

        if not is_success or not is_clean:
            recent_attention.append(_attention_item(event, is_success=is_success))

    active_identity_keys = set().union(*identity_keys_by_type.values())
    returning_identity_keys = {
        identity_key
        for identity_key in active_identity_keys
        if identity_key in prior_identity_keys or len(identity_dates[identity_key]) > 1
    }
    total = len(events)
    failure_count = total - success_count
    recent_attention.sort(key=lambda item: str(item["created_at"]), reverse=True)

    top_errors = [
        {"error_code": error_code, "error_stage": error_stage, "count": count}
        for (error_code, error_stage), count in sorted(
            top_error_counts.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )[:5]
    ]
    layout_items = []
    for item in sorted(layouts.values(), key=lambda value: (-int(value["conversions"]), str(value["layout_name"])))[:10]:
        confidence_count = int(item.pop("confidence_count"))
        confidence_sum = float(item.pop("confidence_sum"))
        item["average_confidence"] = round(confidence_sum / confidence_count, 4) if confidence_count else None
        item["clean_high_confidence_rate"] = _percentage(int(item["clean_high_confidence"]), int(item["conversions"]))
        layout_items.append(item)

    user_usage = _build_user_usage(
        events,
        prior_identity_keys=prior_identity_keys,
        registered_profiles=registered_profiles,
    )

    return {
        "days": days,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "timezone": DASHBOARD_TIMEZONE_NAME,
        "summary": {
            "conversions_total": total,
            "non_conversion_count": non_conversion_count,
            "pages_total": pages_total,
            "pdf_conversions_count": pdf_conversions_count,
            "pdf_pages_count": pdf_pages_count,
            "ocr_conversions_count": ocr_conversions_count,
            "ocr_pages_count": ocr_pages_count,
            "technical_success_count": success_count,
            "technical_success_rate": _percentage(success_count, total),
            "clean_conversion_count": clean_count,
            "clean_conversion_rate": _percentage(clean_count, total),
            "clean_high_confidence_count": clean_count,
            "clean_high_confidence_rate": _percentage(clean_count, total),
            "review_count": review_count,
            "failure_count": failure_count,
            "active_people_count": len(active_identity_keys),
            "returning_people_count": len(returning_identity_keys),
            "median_duration_ms": int(median(durations)) if durations else 0,
        },
        "identities": {
            "registered_conversions": conversion_counts_by_type["registered"],
            "registered_people": len(identity_keys_by_type["registered"]),
            "anonymous_conversions": conversion_counts_by_type["anonymous"],
            "anonymous_people": len(identity_keys_by_type["anonymous"]),
        },
        "daily": list(daily_by_date.values()),
        "top_errors": top_errors,
        "top_quality_issues": top_quality_issues,
        "canonical_capture": _build_canonical_capture_summary(events),
        "native_geometry_shadow": _build_native_geometry_shadow_summary(native_geometry_events),
        "checkout_funnel": _build_checkout_funnel(checkout_intents, product_events),
        "commercial_interest": _build_commercial_interest(product_events, registered_profiles),
        "heavy_users": _rank_user_usage(user_usage, sort_field="pages"),
        "returning_heavy_users": _rank_user_usage(
            [item for item in user_usage if bool(item["is_returning"])],
            sort_field="pages",
        ),
        "ocr_heavy_users": _rank_user_usage(
            [item for item in user_usage if int(item["ocr_pages"]) > 0],
            sort_field="ocr_pages",
        ),
        "layouts": layout_items,
        "recent_attention": recent_attention[:10],
    }


def _build_native_geometry_shadow_summary(events: list[dict[str, object]]) -> dict[str, object]:
    classification_counts = Counter(str(event.get("classification") or "inconclusive") for event in events)
    durations = [_as_non_negative_int(event.get("geometry_duration_ms")) for event in events]
    layouts: dict[str, dict[str, object]] = {}
    for event in events:
        layout_name = str(
            event.get("baseline_layout")
            or event.get("geometry_layout")
            or "Não identificado"
        )
        item = layouts.setdefault(
            layout_name,
            {
                "layout_name": layout_name,
                "evaluated": 0,
                "potential_rescues": 0,
                "potential_gains": 0,
                "conflicts": 0,
                "errors": 0,
                "durations": [],
            },
        )
        classification = str(event.get("classification") or "inconclusive")
        item["evaluated"] = int(item["evaluated"]) + 1
        if classification == "potential_rescue":
            item["potential_rescues"] = int(item["potential_rescues"]) + 1
        if classification == "potential_gain":
            item["potential_gains"] = int(item["potential_gains"]) + 1
        if classification == "conflict":
            item["conflicts"] = int(item["conflicts"]) + 1
        if str(event.get("geometry_status") or "error") == "error":
            item["errors"] = int(item["errors"]) + 1
        item["durations"].append(_as_non_negative_int(event.get("geometry_duration_ms")))

    by_layout: list[dict[str, object]] = []
    for item in sorted(
        layouts.values(),
        key=lambda value: (-int(value["evaluated"]), str(value["layout_name"])),
    )[:10]:
        layout_durations = list(item.pop("durations"))
        item["median_duration_ms"] = int(median(layout_durations)) if layout_durations else 0
        by_layout.append(item)

    recent = [
        {
            "processing_id": str(event.get("processing_id") or ""),
            "identity_type": str(event.get("identity_type") or ""),
            "created_at": str(event.get("created_at") or ""),
            "classification": str(event.get("classification") or "inconclusive"),
            "baseline_layout": str(event.get("baseline_layout") or "") or None,
            "geometry_layout": str(event.get("geometry_layout") or "") or None,
            "baseline_transactions": _as_non_negative_int(event.get("baseline_transactions")),
            "geometry_transactions": _as_non_negative_int(event.get("geometry_transactions")),
            "geometry_duration_ms": _as_non_negative_int(event.get("geometry_duration_ms")),
            "date_conflicts": _as_non_negative_int(event.get("date_conflicts")),
            "amount_conflicts": _as_non_negative_int(event.get("amount_conflicts")),
            "sign_conflicts": _as_non_negative_int(event.get("sign_conflicts")),
            "geometry_error_type": str(event.get("geometry_error_type") or "") or None,
        }
        for event in events[:10]
    ]
    return {
        "evaluated_count": len(events),
        "success_count": sum(1 for event in events if str(event.get("geometry_status") or "") == "ok"),
        "potential_rescue_count": classification_counts["potential_rescue"],
        "potential_gain_count": classification_counts["potential_gain"],
        "equivalent_count": classification_counts["equivalent"],
        "regression_count": classification_counts["regression"],
        "conflict_count": classification_counts["conflict"],
        "error_count": sum(
            1 for event in events if str(event.get("geometry_status") or "error") == "error"
        ),
        "shadow_error_count": classification_counts["shadow_error"],
        "inconclusive_count": classification_counts["inconclusive"],
        "not_applicable_count": classification_counts["not_applicable"],
        "median_duration_ms": int(median(durations)) if durations else 0,
        "p95_duration_ms": _nearest_rank_percentile(durations, 0.95),
        "by_layout": by_layout,
        "recent": recent,
    }


def _nearest_rank_percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, int(len(ordered) * percentile + 0.999999))
    return int(ordered[min(rank, len(ordered)) - 1])


def _build_user_usage(
    events: list[dict[str, object]],
    *,
    prior_identity_keys: set[str],
    registered_profiles: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for event in events:
        identity_key = str(event["identity_key"])
        identity_type = str(event["identity_type"])
        identity_id = str(event["identity_id"])
        profile = registered_profiles.get(identity_id, {}) if identity_type == "registered" else {}
        if identity_type == "registered":
            identity_reference = identity_id
            display_name = profile.get("name") or "Pessoa cadastrada"
            email = profile.get("email") or None
        else:
            identity_reference = f"anon_{sha256(identity_id.encode('utf-8')).hexdigest()[:12]}"
            display_name = f"Anônima {identity_reference[-6:]}"
            email = None

        item = grouped.setdefault(
            identity_key,
            {
                "rank": 0,
                "raw_identity_key": identity_key,
                "identity_type": identity_type,
                "identity_reference": identity_reference,
                "display_name": display_name,
                "email": email,
                "conversions": 0,
                "pages": 0,
                "pdf_conversions": 0,
                "pdf_pages": 0,
                "ocr_conversions": 0,
                "ocr_pages": 0,
                "successes": 0,
                "review": 0,
                "failures": 0,
                "transactions": 0,
                "activity_dates": set(),
                "last_activity_at": None,
                "model_counts": Counter(),
            },
        )
        pages_count = int(event["pages_count"])
        item["conversions"] = int(item["conversions"]) + 1
        item["pages"] = int(item["pages"]) + pages_count
        item["transactions"] = int(item["transactions"]) + int(event["transactions_count"])
        model_counts = item["model_counts"]
        if isinstance(model_counts, Counter):
            model_counts[str(event["model"] or "Não identificado")] += 1
        if bool(event["ocr_used"]):
            item["ocr_conversions"] = int(item["ocr_conversions"]) + 1
            item["ocr_pages"] = int(item["ocr_pages"]) + pages_count
        else:
            item["pdf_conversions"] = int(item["pdf_conversions"]) + 1
            item["pdf_pages"] = int(item["pdf_pages"]) + pages_count

        is_success = _is_success_status(str(event["status"]))
        if is_success:
            item["successes"] = int(item["successes"]) + 1
            if str(event["quality_status"]) != "clean":
                item["review"] = int(item["review"]) + 1
        else:
            item["failures"] = int(item["failures"]) + 1

        created_at = _parse_datetime(str(event["created_at"]))
        if created_at:
            local_created_at = created_at.astimezone(DASHBOARD_TIMEZONE).isoformat()
            activity_dates = item["activity_dates"]
            if isinstance(activity_dates, set):
                activity_dates.add(local_created_at[:10])
            if not item["last_activity_at"] or local_created_at > str(item["last_activity_at"]):
                item["last_activity_at"] = local_created_at

    usage_items: list[dict[str, object]] = []
    for identity_key, item in grouped.items():
        activity_dates = item.pop("activity_dates")
        active_days = len(activity_dates) if isinstance(activity_dates, set) else 0
        item["active_days"] = active_days
        item["is_returning"] = identity_key in prior_identity_keys or active_days > 1
        item["ocr_share_rate"] = _percentage(int(item["ocr_pages"]), int(item["pages"]))
        model_counts = item.pop("model_counts")
        item["top_models"] = [
            {"model": model, "count": count}
            for model, count in sorted(
                model_counts.items(),
                key=lambda value: (-int(value[1]), str(value[0])),
            )[:3]
        ] if isinstance(model_counts, Counter) else []
        usage_items.append(item)
    return usage_items


def _enrich_active_users(
    items: list[dict[str, object]],
    *,
    product_events: list[dict[str, str]],
    registered_profiles: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    product_by_identity: dict[str, Counter[str]] = defaultdict(Counter)
    formats_by_identity: dict[str, Counter[str]] = defaultdict(Counter)
    for event in product_events:
        identity_key = f"{event['identity_type']}:{event['identity_id']}"
        product_by_identity[identity_key][event["event_type"]] += 1
        if event["event_type"] == "download" and event["download_format"]:
            formats_by_identity[identity_key][event["download_format"]] += 1

    enriched: list[dict[str, object]] = []
    for original in items:
        item = dict(original)
        identity_type = str(item["identity_type"])
        identity_id = str(item["identity_reference"])
        raw_key = str(item.pop("raw_identity_key", ""))
        counters = product_by_identity.get(raw_key, Counter())
        profile = registered_profiles.get(identity_id, {}) if identity_type == "registered" else {}
        item["plan_code"] = str(profile.get("plan_code") or "free")
        item["marketing_contact_allowed"] = bool(profile.get("marketing_contact_allowed", False))
        item["plans_page_views"] = int(counters["plans_view"])
        item["plan_cta_clicks"] = int(counters["plan_cta_click"])
        item["checkout_entries"] = int(counters["checkout_view"])
        item["downloads_total"] = int(counters["download"])
        item["download_formats"] = [
            {"format": download_format, "count": count}
            for download_format, count in sorted(
                formats_by_identity.get(raw_key, Counter()).items(),
                key=lambda value: (-int(value[1]), str(value[0])),
            )
        ]
        score, profile_label, reasons = _score_purchase_profile(item)
        item["purchase_score"] = score
        item["purchase_profile"] = profile_label
        item["purchase_reasons"] = reasons
        enriched.append(item)
    return enriched


def _score_purchase_profile(item: dict[str, object]) -> tuple[int, str, list[str]]:
    if str(item.get("plan_code") or "free") != "free":
        return 0, "cliente", ["já possui plano pago"]
    score = 0
    reasons: list[str] = []
    pages = int(item.get("pages") or 0)
    if pages >= 100:
        score += 40
        reasons.append("alto volume de páginas")
    elif pages >= 50:
        score += 30
        reasons.append("volume relevante de páginas")
    elif pages >= 20:
        score += 20
        reasons.append("volume crescente de páginas")
    elif pages >= 10:
        score += 10
        reasons.append("uso acima do básico")
    if bool(item.get("is_returning")):
        score += 15
        reasons.append("uso recorrente")
    if int(item.get("conversions") or 0) >= 5:
        score += 10
        reasons.append("múltiplas conversões")
    if int(item.get("downloads_total") or 0) > 0:
        score += 10
        reasons.append("baixou resultado")
    if int(item.get("plans_page_views") or 0) > 0:
        score += 5
        reasons.append("visitou planos")
    if int(item.get("plan_cta_clicks") or 0) > 0:
        score += 15
        reasons.append("clicou em um plano")
    if int(item.get("checkout_entries") or 0) > 0:
        score += 25
        reasons.append("entrou no checkout")
    score = min(score, 100)
    label = "quente" if score >= 60 else "morno" if score >= 35 else "acompanhar"
    return score, label, reasons or ["atividade recente"]


def _build_active_users_summary(items: list[dict[str, object]]) -> dict[str, int]:
    return {
        "active_people": len(items),
        "registered_people": sum(1 for item in items if item["identity_type"] == "registered"),
        "anonymous_people": sum(1 for item in items if item["identity_type"] == "anonymous"),
        "pages_total": sum(int(item["pages"]) for item in items),
        "successes": sum(int(item["successes"]) for item in items),
        "failures": sum(int(item["failures"]) for item in items),
        "hot_prospects": sum(1 for item in items if item["purchase_profile"] == "quente"),
        "checkout_people": sum(1 for item in items if int(item["checkout_entries"]) > 0),
        "download_people": sum(1 for item in items if int(item["downloads_total"]) > 0),
    }


def _rank_user_usage(
    items: list[dict[str, object]],
    *,
    sort_field: str,
) -> list[dict[str, object]]:
    ranked = sorted(
        items,
        key=lambda item: (
            -int(item[sort_field]),
            -int(item["pages"]),
            -int(item["conversions"]),
            str(item["identity_reference"]),
        ),
    )[:10]
    for rank, item in enumerate(ranked, start=1):
        item = dict(item)
        item.pop("raw_identity_key", None)
        item["rank"] = rank
        ranked[rank - 1] = item
    return ranked


def _build_checkout_funnel(
    checkout_intents: list[dict[str, str]],
    product_events: list[dict[str, str]],
) -> dict[str, object]:
    requested_statuses = {"REQUESTED", "PENDING"}
    awaiting_status = "AWAITING_PAYMENT"
    released_status = "RELEASED_FOR_USE"

    def person_key(item: dict[str, str]) -> str:
        user_id = str(item.get("user_id") or "").strip()
        if user_id:
            return f"user:{user_id}"
        return f"email:{str(item.get('customer_email') or '').strip().lower()}"

    people = {person_key(item) for item in checkout_intents if person_key(item) != "email:"}
    released_people = {
        person_key(item)
        for item in checkout_intents
        if item.get("status") == released_status and person_key(item) != "email:"
    }
    product_counts = Counter(event["event_type"] for event in product_events)

    def product_people(event_type: str) -> int:
        return len(
            {
                f"{event['identity_type']}:{event['identity_id']}"
                for event in product_events
                if event["event_type"] == event_type
            }
        )

    download_formats = Counter(
        event["download_format"]
        for event in product_events
        if event["event_type"] == "download" and event["download_format"]
    )
    return {
        "plans_page_views_count": int(product_counts["plans_view"]),
        "plans_page_people_count": product_people("plans_view"),
        "plan_cta_clicks_count": int(product_counts["plan_cta_click"]),
        "plan_cta_people_count": product_people("plan_cta_click"),
        "checkout_entries_count": int(product_counts["checkout_view"]),
        "checkout_entry_people_count": product_people("checkout_view"),
        "downloads_count": int(product_counts["download"]),
        "download_people_count": product_people("download"),
        "download_formats": [
            {"format": download_format, "count": count}
            for download_format, count in sorted(
                download_formats.items(),
                key=lambda value: (-int(value[1]), str(value[0])),
            )
        ],
        "checkout_intents_count": len(checkout_intents),
        "checkout_people_count": len(people),
        "requested_intents_count": sum(1 for item in checkout_intents if item.get("status") in requested_statuses),
        "awaiting_payment_intents_count": sum(
            1 for item in checkout_intents if item.get("status") == awaiting_status
        ),
        "released_intents_count": sum(1 for item in checkout_intents if item.get("status") == released_status),
        "released_people_count": len(released_people),
        "checkout_to_release_rate": _percentage(len(released_people), len(people)),
    }


def _registered_product_identity_ids(product_events: list[dict[str, str]]) -> set[str]:
    return {
        event["identity_id"]
        for event in product_events
        if event["identity_type"] == "registered" and event["identity_id"]
    }


def _build_commercial_interest(
    product_events: list[dict[str, str]],
    registered_profiles: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for event in product_events:
        if event["event_type"] not in {"plans_view", "plan_cta_click", "checkout_view"}:
            continue
        identity_type = event["identity_type"]
        identity_id = event["identity_id"]
        identity_key = f"{identity_type}:{identity_id}"
        profile = registered_profiles.get(identity_id, {}) if identity_type == "registered" else {}
        identity_reference = (
            identity_id
            if identity_type == "registered"
            else f"anon_{sha256(identity_id.encode('utf-8')).hexdigest()[:12]}"
        )
        item = grouped.setdefault(
            identity_key,
            {
                "identity_type": identity_type,
                "identity_reference": identity_reference,
                "display_name": profile.get("name") or (
                    "Pessoa cadastrada" if identity_type == "registered" else f"Anônima {identity_reference[-6:]}"
                ),
                "email": profile.get("email") or None,
                "marketing_contact_allowed": bool(profile.get("marketing_contact_allowed", False)),
                "plans_page_views": 0,
                "plan_cta_clicks": 0,
                "checkout_entries": 0,
                "plan_codes": set(),
                "last_interest_at": None,
            },
        )
        metric = {
            "plans_view": "plans_page_views",
            "plan_cta_click": "plan_cta_clicks",
            "checkout_view": "checkout_entries",
        }[event["event_type"]]
        item[metric] = int(item[metric]) + 1
        plan_codes = item["plan_codes"]
        if event["plan_code"] and isinstance(plan_codes, set):
            plan_codes.add(event["plan_code"])
        if not item["last_interest_at"] or event["created_at"] > str(item["last_interest_at"]):
            item["last_interest_at"] = event["created_at"]

    result: list[dict[str, object]] = []
    for item in grouped.values():
        plan_codes = item["plan_codes"]
        item["plan_codes"] = sorted(plan_codes) if isinstance(plan_codes, set) else []
        result.append(item)
    result.sort(
        key=lambda item: (
            -int(item["checkout_entries"]),
            -int(item["plan_cta_clicks"]),
            -int(item["plans_page_views"]),
            -(_parse_datetime(str(item["last_interest_at"] or "")).timestamp() if item["last_interest_at"] else 0.0),
        ),
    )
    return result[:20]


def _attention_item(event: dict[str, object], *, is_success: bool) -> dict[str, object]:
    reasons: list[str] = []
    if not is_success:
        reasons.append("Falha técnica")
    if int(event["transactions_count"]) <= 0:
        reasons.append("Nenhuma transação encontrada")
    warning_count = int(event["warning_count"])
    if warning_count > 0:
        reasons.append(_count_label(warning_count, "transação com alerta", "transações com alerta"))
    balance_failed = int(event["balance_failed"])
    if balance_failed > 0:
        reasons.append(_count_label(balance_failed, "inconsistência de saldo", "inconsistências de saldo"))
    reason_labels = {
        "generic_layout": "Layout genérico ou não identificado",
        "layout_confidence_missing": "Score de reconhecimento ausente",
        "layout_confidence_below_95": "Score de reconhecimento abaixo de 95%",
        "parser_missing": "Parser não identificado",
        "row_warnings": "Linhas com alerta",
        "balance_inconsistency": "Inconsistência de saldo",
        "no_transactions": "Nenhuma transação encontrada",
        "technical_failure": "Falha técnica",
    }
    for code in event.get("quality_reason_codes", []):
        label = reason_labels.get(str(code), str(code))
        if label not in reasons:
            reasons.append(label)

    return {
        "processing_id": str(event["processing_id"]),
        "identity_type": str(event["identity_type"]),
        "created_at": str(event["created_at"]),
        "model": str(event["model"]),
        "conversion_type": str(event["conversion_type"]),
        "status": str(event["status"]),
        "transactions_count": int(event["transactions_count"]),
        "duration_ms": int(event["duration_ms"]),
        "error_code": event["error_code"],
        "error_stage": event["error_stage"],
        "layout_name": event.get("layout_name"),
        "layout_confidence": event.get("layout_confidence"),
        "selected_parser": event.get("selected_parser"),
        "quality_status": event.get("quality_status"),
        "quality_reason_codes": event.get("quality_reason_codes", []),
        "canonical_capture_status": event.get("canonical_capture_status"),
        "canonical_capture_reason": event.get("canonical_capture_reason"),
        "issue_reason": "; ".join(reasons) or "Revisão recomendada",
    }


def _build_canonical_capture_summary(events: list[dict[str, object]]) -> dict[str, object]:
    counts = Counter(str(event.get("canonical_capture_status") or "not_recorded") for event in events)
    reason_counts = Counter(
        (
            str(event.get("canonical_capture_status") or "not_recorded"),
            str(event.get("canonical_capture_reason") or "").strip(),
        )
        for event in events
        if str(event.get("canonical_capture_reason") or "").strip()
    )
    known_statuses = set(CANONICAL_CAPTURE_STATUS_ORDER)
    ordered_statuses = [status for status in CANONICAL_CAPTURE_STATUS_ORDER if counts[status] > 0]
    ordered_statuses.extend(sorted(status for status in counts if status not in known_statuses))
    return {
        "candidate_count": sum(counts[status] for status in CANONICAL_CAPTURE_CANDIDATE_STATUSES),
        "stored_count": counts["stored"],
        "failure_count": sum(counts[status] for status in CANONICAL_CAPTURE_FAILURE_STATUSES),
        "skipped_count": sum(counts[status] for status in CANONICAL_CAPTURE_SKIPPED_STATUSES),
        "not_eligible_count": counts["not_eligible"] + counts["not_attempted"],
        "disabled_count": counts["disabled"],
        "not_recorded_count": counts["not_recorded"],
        "by_status": [{"status": status, "count": counts[status]} for status in ordered_statuses],
        "by_reason": [
            {"status": status, "reason": reason, "count": count}
            for (status, reason), count in sorted(
                reason_counts.items(),
                key=lambda item: (-item[1], item[0][0], item[0][1]),
            )
        ],
    }


def _is_success_status(status: str) -> bool:
    return status.strip().casefold() in {"sucesso", "success", "completed"}


def _is_non_conversion_event(event: dict[str, object]) -> bool:
    return str(event.get("error_code") or "").strip().casefold() in NON_CONVERSION_ERROR_CODES


def _percentage(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((count / total) * 100, 1)


def _count_label(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def _as_non_negative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _as_optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_string_list(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _as_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_datetime(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return _as_aware_utc(parsed)
