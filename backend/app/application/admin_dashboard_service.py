from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


DASHBOARD_TIMEZONE_NAME = "America/Sao_Paulo"
DASHBOARD_TIMEZONE = ZoneInfo(DASHBOARD_TIMEZONE_NAME)
SUPPORTED_IDENTITY_TYPES = frozenset({"all", "registered", "anonymous"})


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

        return _build_dashboard_payload(
            events=events,
            prior_identity_keys=prior_identity_keys,
            days=normalized_days,
            start_date=start_date,
            start_at=start_utc,
            end_at=now_utc,
        )

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
                    duration_ms,
                    error_code,
                    error_stage,
                    canonical_warning_transactions_count,
                    balance_consistency_failed
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
                    duration_ms,
                    error_code,
                    error_stage,
                    canonical_warning_transactions_count,
                    balance_consistency_failed
                FROM anonymous_conversion_events
                WHERE created_at >= ? AND created_at <= ?
                """,
                (start_at, end_at),
            )
            events.extend(_row_to_event(row, identity_type="anonymous") for row in rows)

        return events

    def _load_prior_identity_keys(
        self,
        conn,
        *,
        before: str,
        identity_type: str,
    ) -> set[str]:
        identity_keys: set[str] = set()
        if identity_type in {"all", "registered"}:
            rows = self._service._fetchall(
                conn,
                """
                SELECT DISTINCT user_id AS identity_id
                FROM user_conversions
                WHERE created_at < ?
                """,
                (before,),
            )
            identity_keys.update(f"registered:{row['identity_id']}" for row in rows)

        if identity_type in {"all", "anonymous"}:
            rows = self._service._fetchall(
                conn,
                """
                SELECT DISTINCT anonymous_fingerprint AS identity_id
                FROM anonymous_conversion_events
                WHERE created_at < ?
                """,
                (before,),
            )
            identity_keys.update(f"anonymous:{row['identity_id']}" for row in rows)
        return identity_keys


def _row_to_event(row, *, identity_type: str) -> dict[str, object]:
    identity_id = str(row["identity_id"] or "")
    return {
        "processing_id": str(row["processing_id"] or ""),
        "identity_type": identity_type,
        "identity_key": f"{identity_type}:{identity_id}",
        "created_at": str(row["created_at"] or ""),
        "model": str(row["model"] or "Não identificado"),
        "conversion_type": str(row["conversion_type"] or "Não identificado"),
        "status": str(row["status"] or ""),
        "transactions_count": _as_non_negative_int(row["transactions_count"]),
        "duration_ms": _as_non_negative_int(row["duration_ms"]),
        "error_code": str(row["error_code"] or "").strip() or None,
        "error_stage": str(row["error_stage"] or "").strip() or None,
        "warning_count": _as_non_negative_int(row["canonical_warning_transactions_count"]),
        "balance_failed": _as_non_negative_int(row["balance_consistency_failed"]),
    }


def _build_dashboard_payload(
    *,
    events: list[dict[str, object]],
    prior_identity_keys: set[str],
    days: int,
    start_date: date,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, object]:
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

    for event in events:
        identity_key = str(event["identity_key"])
        event_identity_type = str(event["identity_type"])
        identity_keys_by_type[event_identity_type].add(identity_key)
        conversion_counts_by_type[event_identity_type] += 1

        created_at = _parse_datetime(str(event["created_at"]))
        local_date = created_at.astimezone(DASHBOARD_TIMEZONE).date().isoformat() if created_at else None
        if local_date:
            identity_dates[identity_key].add(local_date)

        is_success = _is_success_status(str(event["status"]))
        is_clean = is_success and _is_clean_event(event)
        if is_success:
            success_count += 1
            duration_ms = int(event["duration_ms"])
            if duration_ms > 0:
                durations.append(duration_ms)
        if is_clean:
            clean_count += 1

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

    return {
        "days": days,
        "start_at": start_at.isoformat(),
        "end_at": end_at.isoformat(),
        "timezone": DASHBOARD_TIMEZONE_NAME,
        "summary": {
            "conversions_total": total,
            "technical_success_count": success_count,
            "technical_success_rate": _percentage(success_count, total),
            "clean_conversion_count": clean_count,
            "clean_conversion_rate": _percentage(clean_count, total),
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
        "recent_attention": recent_attention[:10],
    }


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
        "issue_reason": "; ".join(reasons) or "Revisão recomendada",
    }


def _is_clean_event(event: dict[str, object]) -> bool:
    return (
        int(event["transactions_count"]) > 0
        and int(event["warning_count"]) == 0
        and int(event["balance_failed"]) == 0
    )


def _is_success_status(status: str) -> bool:
    return status.strip().casefold() in {"sucesso", "success", "completed"}


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
