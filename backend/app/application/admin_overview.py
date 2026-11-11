"""Aggregate admin history in SQL and fetch only 20 interested people."""
from datetime import datetime, time, timedelta, timezone
from hashlib import sha256

from app.application.admin_dashboard_service import (
    DASHBOARD_TIMEZONE,
    DASHBOARD_TIMEZONE_NAME,
    NON_CONVERSION_ERROR_CODES,
    SUPPORTED_IDENTITY_TYPES,
    _as_aware_utc,
    _as_bool,
    _as_non_negative_int,
    _as_optional_float,
    _build_checkout_funnel,
    _parse_datetime,
    _percentage,
)
from app.application.conversion_quality import assess_conversion_quality

_EXCLUDED = ",".join(f"'{code}'" for code in sorted(NON_CONVERSION_ERROR_CODES))
_SUCCESS = "LOWER(TRIM(COALESCE(status,''))) IN ('sucesso','success','completed')"
_QUALITY_FIELDS = ("status, conversion_type, transactions_count, layout_inference_name, layout_inference_confidence, "
                   "selected_parser, canonical_warning_transactions_count, balance_consistency_failed, parser_confidence_band")


class AdminOverviewService:
    def __init__(self, service):
        self.service = service

    def get_overview(self, *, days=30, identity_type="all"):
        days = max(1, min(int(days), 90))
        identity_type = str(identity_type or "all").strip().lower()
        if identity_type not in SUPPORTED_IDENTITY_TYPES:
            raise ValueError("Unsupported identity_type")
        end = _as_aware_utc(self.service.now_provider())
        start_day = end.astimezone(DASHBOARD_TIMEZONE).date()-timedelta(days=days-1)
        start = datetime.combine(start_day, time.min, tzinfo=DASHBOARD_TIMEZONE).astimezone(timezone.utc)
        with self.service._lock, self.service._connect() as conn:
            if not self.service._use_postgres:
                conn.create_function("admin_local_day", 1, lambda value: (
                    parsed.astimezone(DASHBOARD_TIMEZONE).date().isoformat() if (parsed := _parse_datetime(value)) else None))
                conn.create_function("admin_timestamp", 1, lambda value: (
                    parsed.timestamp() if (parsed := _parse_datetime(value)) else None))
            period = (start.isoformat(), end.isoformat())
            summary = self._summary(conn, period, identity_type)
            funnel, client_people = self._funnel(conn, period, identity_type)
            summary["client_area_people_count"] = client_people
            interested = self._interested(conn, period, identity_type)
        return {"days": days, "start_at": start.isoformat(), "end_at": end.isoformat(), "timezone": DASHBOARD_TIMEZONE_NAME,
                "summary": summary, "checkout_funnel": funnel, "commercial_interest": interested}

    def _history(self, identity_type):
        tables = [("registered", "user_conversions", "user_id"), ("anonymous", "anonymous_conversion_events", "anonymous_fingerprint")]
        queries = [f"""SELECT '{kind}' AS kind, COALESCE({owner},'') AS identity_id, created_at, error_code,
            COALESCE(pages_count,0) AS pages_count, ocr_used, duration_ms, quality_status, {_QUALITY_FIELDS} FROM {table}"""
            for kind, table, owner in tables if identity_type in ("all", kind)]
        materialization = "NOT MATERIALIZED" if self.service._use_postgres else ""
        return f"""WITH history AS {materialization} ({' UNION ALL '.join(queries)}),
            period AS (SELECT * FROM history WHERE created_at >= ? AND created_at <= ?),
            events AS (SELECT * FROM period WHERE LOWER(TRIM(COALESCE(error_code,''))) NOT IN ({_EXCLUDED})) """

    def _summary(self, conn, period, identity_type):
        cte = self._history(identity_type)
        day = ("(created_at::timestamptz AT TIME ZONE 'America/Sao_Paulo')::date"
               if self.service._use_postgres else "admin_local_day(created_at)")
        row = self.service._fetchone(conn, cte + f"""SELECT COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN pages_count>0 THEN pages_count ELSE 0 END),0) AS pages,
            COALESCE(SUM(CASE WHEN ocr_used THEN 1 ELSE 0 END),0) AS ocr,
            COALESCE(SUM(CASE WHEN ocr_used AND pages_count>0 THEN pages_count ELSE 0 END),0) AS ocr_pages,
            COALESCE(SUM(CASE WHEN {_SUCCESS} THEN 1 ELSE 0 END),0) AS success,
            COALESCE(SUM(CASE WHEN {_SUCCESS} AND TRIM(COALESCE(quality_status,''))='clean' THEN 1 ELSE 0 END),0) AS clean,
            (SELECT COUNT(*) FROM period)-COUNT(*) AS non_conversion,
            (SELECT COUNT(*) FROM (SELECT kind, identity_id FROM events GROUP BY kind, identity_id) people) AS people,
            (SELECT COUNT(*) FROM (
                SELECT kind, identity_id, COUNT(DISTINCT {day}) AS days FROM events GROUP BY kind, identity_id
            ) people WHERE people.days>1 OR EXISTS (
                SELECT 1 FROM history h WHERE h.kind=people.kind AND h.identity_id=people.identity_id
                AND h.created_at < ? AND COALESCE(h.error_code,'') NOT IN ({_EXCLUDED})
            )) AS returning_people FROM events""", (*period, period[0]))
        # Legacy rows have no stored quality. Aggregate identical inputs and use
        # the shared policy, so the lightweight view never invents a new rule.
        legacy = self.service._fetchall(conn, cte + f"""SELECT {_QUALITY_FIELDS}, COUNT(*) AS n FROM events
            WHERE {_SUCCESS} AND TRIM(COALESCE(quality_status,''))='' GROUP BY {_QUALITY_FIELDS}""", period)
        clean = int(row["clean"])
        for item in legacy:
            quality = assess_conversion_quality(status=str(item["status"] or ""), conversion_type=str(item["conversion_type"] or ""),
                transactions_count=_as_non_negative_int(item["transactions_count"]),
                layout_name=str(item["layout_inference_name"] or "") or None,
                layout_confidence=_as_optional_float(item["layout_inference_confidence"]),
                selected_parser=str(item["selected_parser"] or "") or None,
                warning_count=_as_non_negative_int(item["canonical_warning_transactions_count"]),
                balance_failed=_as_non_negative_int(item["balance_consistency_failed"]),
                parser_confidence_band=str(item["parser_confidence_band"] or "") or None)
            if quality.status == "clean":
                clean += int(item["n"])
        duration = self.service._fetchone(conn, cte + f""", durations AS (
            SELECT duration_ms, ROW_NUMBER() OVER (ORDER BY duration_ms) AS position, COUNT(*) OVER () AS n
            FROM events WHERE {_SUCCESS} AND duration_ms>0)
            SELECT AVG(duration_ms) AS value FROM durations WHERE position IN ((n+1)/2, (n+2)/2)""", period)
        total, success = int(row["total"]), int(row["success"])
        return {"conversions_total": total, "non_conversion_count": int(row["non_conversion"]), "pages_total": int(row["pages"]),
                "pdf_conversions_count": total-int(row["ocr"]), "pdf_pages_count": int(row["pages"])-int(row["ocr_pages"]),
                "ocr_conversions_count": int(row["ocr"]), "ocr_pages_count": int(row["ocr_pages"]),
                "technical_success_count": success, "technical_success_rate": _percentage(success, total),
                "clean_conversion_count": clean, "clean_conversion_rate": _percentage(clean, total),
                "clean_high_confidence_count": clean, "clean_high_confidence_rate": _percentage(clean, total),
                "review_count": success-clean, "failure_count": total-success, "active_people_count": int(row["people"]),
                "returning_people_count": int(row["returning_people"]), "median_duration_ms": int(duration["value"] or 0)}

    @staticmethod
    def _product_filter(period, identity_type):
        where = "created_at >= ? AND created_at <= ?"
        return (where, period) if identity_type == "all" else (where+" AND identity_type=?", (*period, identity_type))

    def _funnel(self, conn, period, identity_type):
        result = _build_checkout_funnel([], [])
        where, params = self._product_filter(period, identity_type)
        events = self.service._fetchall(conn, f"""SELECT event_type, COUNT(*) AS n,
            COUNT(DISTINCT COALESCE(identity_type,'') || ':' || COALESCE(identity_id,'')) AS people,
            COUNT(DISTINCT CASE WHEN identity_type='registered' AND identity_id<>'' THEN identity_id END) AS registered
            FROM product_events WHERE {where} GROUP BY event_type""", params)
        fields = {"plans_view": ("plans_page_views_count", "plans_page_people_count"),
                  "plan_cta_click": ("plan_cta_clicks_count", "plan_cta_people_count"),
                  "checkout_view": ("checkout_entries_count", "checkout_entry_people_count"),
                  "download": ("downloads_count", "download_people_count")}
        client_people = 0
        for row in events:
            if row["event_type"] in fields:
                count, people = fields[row["event_type"]]
                result[count], result[people] = int(row["n"]), int(row["people"])
            if row["event_type"] == "client_area_view":
                client_people = int(row["registered"])
        result["download_formats"] = [dict(row) for row in self.service._fetchall(conn, f"""
            SELECT download_format AS format, COUNT(*) AS count FROM product_events
            WHERE {where} AND event_type='download' AND download_format<>''
            GROUP BY download_format ORDER BY count DESC, format""", params)]
        if identity_type != "anonymous":
            order = self.service._fetchone(conn, """WITH orders AS (
                SELECT UPPER(TRIM(status)) AS status, CASE WHEN TRIM(COALESCE(user_id,''))<>'' THEN 'user:' || TRIM(user_id)
                    ELSE 'email:' || LOWER(TRIM(COALESCE(customer_email,''))) END AS person
                FROM checkout_intents WHERE created_at >= ? AND created_at <= ?)
                SELECT COUNT(*) AS intents, COUNT(DISTINCT CASE WHEN person<>'email:' THEN person END) AS people,
                    SUM(CASE WHEN status IN ('REQUESTED','PENDING') THEN 1 ELSE 0 END) AS requested,
                    SUM(CASE WHEN status='AWAITING_PAYMENT' THEN 1 ELSE 0 END) AS awaiting,
                    SUM(CASE WHEN status='RELEASED_FOR_USE' THEN 1 ELSE 0 END) AS released,
                    COUNT(DISTINCT CASE WHEN status='RELEASED_FOR_USE' AND person<>'email:' THEN person END) AS released_people
                FROM orders""", period)
            for key, field in (("checkout_intents_count", "intents"), ("checkout_people_count", "people"),
                ("requested_intents_count", "requested"), ("awaiting_payment_intents_count", "awaiting"),
                ("released_intents_count", "released"), ("released_people_count", "released_people")):
                result[key] = int(order[field] or 0)
            result["checkout_to_release_rate"] = _percentage(result["released_people_count"], result["checkout_people_count"])
        return result, client_people

    def _interested(self, conn, period, identity_type):
        where, params = self._product_filter(period, identity_type)
        stamp = "MAX(created_at)::timestamptz" if self.service._use_postgres else "admin_timestamp(MAX(created_at))"
        rows = self.service._fetchall(conn, f"""SELECT identity_type, identity_id,
            SUM(CASE WHEN event_type='plans_view' THEN 1 ELSE 0 END) AS plans_page_views,
            SUM(CASE WHEN event_type='plan_cta_click' THEN 1 ELSE 0 END) AS plan_cta_clicks,
            SUM(CASE WHEN event_type='checkout_view' THEN 1 ELSE 0 END) AS checkout_entries,
            MAX(created_at) AS last_interest_at FROM product_events
            WHERE {where} AND event_type IN ('plans_view','plan_cta_click','checkout_view')
            GROUP BY identity_type, identity_id ORDER BY checkout_entries DESC, plan_cta_clicks DESC,
            plans_page_views DESC, {stamp} DESC LIMIT 20""", params)
        if not rows:
            return []
        owners = {str(row["identity_id"]) for row in rows if row["identity_type"] == "registered"}
        profiles = {}
        if owners:
            profiles = {str(row["id"]): dict(row) for row in self.service._fetchall(conn,
                f"SELECT id, name, email, product_updates_opt_in FROM users WHERE id IN ({','.join('?' for _ in owners)})", tuple(owners))}
        selected = " OR ".join("(identity_type=? AND identity_id=?)" for _ in rows)
        plans = self.service._fetchall(conn, f"""SELECT DISTINCT identity_type, identity_id, plan_code FROM product_events
            WHERE {where} AND event_type IN ('plans_view','plan_cta_click','checkout_view') AND plan_code<>'' AND ({selected})""",
            (*params, *(value for row in rows for value in (row["identity_type"], row["identity_id"]))))
        result = []
        for row in rows:
            kind, owner = str(row["identity_type"] or ""), str(row["identity_id"] or "")
            reference = owner if kind == "registered" else f"anon_{sha256(owner.encode()).hexdigest()[:12]}"
            profile = profiles.get(owner, {}) if kind == "registered" else {}
            result.append({**dict(row), "identity_reference": reference,
                "display_name": profile.get("name") or ("Pessoa cadastrada" if kind == "registered" else f"Anônima {reference[-6:]}"),
                "email": profile.get("email") or None, "marketing_contact_allowed": _as_bool(profile.get("product_updates_opt_in")),
                "plan_codes": sorted({plan["plan_code"] for plan in plans
                                      if (plan["identity_type"], plan["identity_id"]) == (kind, owner)})})
            result[-1].pop("identity_id")  # Never expose an anonymous browser identifier.
        return result
