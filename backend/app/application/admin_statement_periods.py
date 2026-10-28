"""Read existing result metadata; never infer a statement period from an upload or assignment."""
import json
from collections import Counter
from datetime import date, datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from app.application.statement_period_history import normalize_statement_period

_TIMEZONE = ZoneInfo("America/Sao_Paulo")
_BUCKETS = (
    ("current_month", "Mesmo mês do envio"), ("previous_month", "Mês anterior ao envio"),
    ("two_to_three_months", "2 a 3 meses antes"), ("four_to_twelve_months", "4 a 12 meses antes"),
    ("older_than_twelve_months", "Mais de 12 meses antes"), ("multiple_months", "Intervalo com vários meses"),
    ("future", "Mês posterior ao envio"), ("unknown", "Não identificado"),
)


def attach_existing_statement_periods(service, conn, events, *, end_at):
    relevant = {(event["identity_type"], event["identity_id"], event["processing_id"]): event
                for event in events if event.get("document_type") == "bank_statement"
                and not normalize_statement_period(event.get("statement_period"))}
    if not relevant:
        return
    if service._use_postgres:
        schema = service.database_schema
        table = f'"{schema}"."conversion_jobs"'
        exists = service._fetchone(conn, "SELECT to_regclass(?) AS present", (table,))
        if not exists or not exists["present"]:
            return
        period = "result_payload #> '{organization_source,period}'"
        owner_type = "identity ->> 'identity_type'"
        owner_id = "identity ->> 'identity_id'"
    else:
        exists = service._fetchone(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='conversion_jobs'")
        if not exists:
            return
        table = "conversion_jobs"
        period = "json_extract(result_payload, '$.organization_source.period')"
        owner_type = "json_extract(identity, '$.identity_type')"
        owner_id = "json_extract(identity, '$.identity_id')"
    # Project only the small immutable period object; no transaction arrays,
    # original PDFs, storage objects or manually assigned pro_periods are read.
    identifiers = sorted({f"an_{key[2][9:21]}" if key[0] == "anonymous" else key[2] for key in relevant})
    rows = []
    for offset in range(0, len(identifiers), 400):
        chunk = identifiers[offset:offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(service._fetchall(conn, f"""SELECT job_id, result_analysis_id,
            {owner_type} AS owner_type, {owner_id} AS owner_id, {period} AS period
            FROM {table} WHERE expires_at > ? AND created_at <= ?
            AND result_analysis_id IN ({placeholders}) AND {period} IS NOT NULL""", (end_at, end_at, *chunk)))
    found = {}
    for row in rows:
        value = row["period"]
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                continue
        if not isinstance(value, dict):
            continue
        kind = {"user": "registered", "anonymous": "anonymous"}.get(row["owner_type"])
        if kind is None:
            continue
        digest = sha256(str(row["job_id"]).encode()).hexdigest()
        aliases = ([str(row["result_analysis_id"] or ""), f"an_{digest[:12]}"] if kind == "registered"
                   else [f"anon_evt_{digest[:24]}"])
        for identifier in set(aliases):
            key = (kind, str(row["owner_id"] or ""), identifier)
            if key in relevant:
                # Conflicting matches must not silently choose a document.
                found[key] = value if key not in found or found[key] == value else None
    for key, value in found.items():
        relevant[key]["statement_period"] = value


def _period(value):
    if (not isinstance(value, dict) or not isinstance(value.get("basis"), str)
            or value["basis"] not in {"document_metadata", "movement_dates"}):
        return None
    try:
        start, end = (date.fromisoformat(value[key]) for key in ("start_date", "end_date"))
    except (ValueError, TypeError, KeyError):
        return None
    if start > end:
        return None
    return start, end, value["basis"]


def _age(start, end, created_at):
    if start[:7] != end[:7]:
        return "multiple_months"
    try:
        uploaded = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if uploaded.tzinfo is None:
            return "unknown"
        local = uploaded.astimezone(_TIMEZONE)
    except (ValueError, TypeError):
        return "unknown"
    year, month = map(int, end[:7].split("-"))
    age = (local.year-year)*12 + local.month-month
    if age < 0:
        return "future"
    if age == 0:
        return "current_month"
    if age == 1:
        return "previous_month"
    if age <= 3:
        return "two_to_three_months"
    if age <= 12:
        return "four_to_twelve_months"
    return "older_than_twelve_months"


def build_statement_period_summary(events):
    statements = [event for event in events if event.get("document_type") == "bank_statement"]
    total = len(statements)
    ages, ranges, sources = Counter(), {}, Counter()
    for event in statements:
        period = _period(event.get("statement_period"))
        if period is None:
            ages["unknown"] += 1
            continue
        start, end, basis = period
        start_month, end_month = start.isoformat()[:7], end.isoformat()[:7]
        sources[basis] += 1
        ages[_age(start.isoformat(), end.isoformat(), event.get("created_at"))] += 1
        key = f"{start_month}:{end_month}"
        item = ranges.setdefault(key, {"key": key, "label": _label(start_month, end_month), "attempts": 0,
                                       "document_metadata_count": 0, "movement_dates_count": 0})
        item["attempts"] += 1
        item[f"{basis}_count"] += 1
    identified = sources.total()
    unknown = total-identified
    by_period = sorted(ranges.values(), key=lambda item: (-item["attempts"], item["key"]))
    if len(by_period) > 24:
        rest = by_period[24:]
        by_period = by_period[:24] + [{"key": "other", "label": "Outros intervalos identificados",
            "attempts": sum(item["attempts"] for item in rest),
            "document_metadata_count": sum(item["document_metadata_count"] for item in rest),
            "movement_dates_count": sum(item["movement_dates_count"] for item in rest)}]
    if unknown:
        by_period.append({"key": "unknown", "label": "Não identificado", "attempts": unknown,
                          "document_metadata_count": 0, "movement_dates_count": 0})
    for item in by_period:
        item["percentage"] = _rate(item["attempts"], total)
    return {"total_count": total, "identified_count": identified, "unknown_count": unknown,
            "coverage_rate": _rate(identified, total), "excluded_count": len(events)-total,
            "document_metadata_count": sources["document_metadata"], "movement_dates_count": sources["movement_dates"],
            "age_buckets": [{"key": key, "label": label, "attempts": ages[key], "percentage": _rate(ages[key], total)}
                            for key, label in _BUCKETS], "by_period": by_period}


def _rate(count, total):
    return round(100*count/total, 2) if total else 0.0


def _label(start, end):
    def month(value):
        return f"{value[5:7]}/{value[:4]}"
    return month(start) if start == end else f"{month(start)} a {month(end)}"
