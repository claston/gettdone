"""Admin periods use persisted documentary evidence, with explicit coverage gaps."""
import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.main import app
from tests.test_admin_dashboard_api import _build_dashboard_client, _record_anonymous_conversion, _record_user_conversion

NOW = datetime(2026, 10, 27, 20, tzinfo=timezone.utc)


def job(service, *, job_id, owner, period, kind="user", expired=False):
    digest = hashlib.sha256(job_id.encode()).hexdigest()
    with service._connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS conversion_jobs (
            job_id TEXT PRIMARY KEY, result_analysis_id TEXT, identity TEXT, result_payload TEXT,
            created_at TEXT, expires_at TEXT)""")
        conn.execute("INSERT INTO conversion_jobs VALUES (?,?,?,?,?,?)", (job_id, f"an_{digest[:12]}",
            json.dumps({"identity_type": kind, "identity_id": owner}), json.dumps({"organization_source": {"period": period}}),
            NOW.isoformat(), "2026-10-26T20:00:00+00:00" if expired else "2026-11-01T20:00:00+00:00"))
        conn.commit()
    return f"an_{digest[:12]}" if kind == "user" else f"anon_evt_{digest[:24]}"


def period(start, end, basis="document_metadata"):
    return {"start_date": start, "end_date": end, "basis": basis, "document_coverage": basis == "document_metadata"}


def test_dashboard_joins_existing_job_metadata_for_registered_and_anonymous_without_rewriting_history(tmp_path):
    client, service, clock, user = _build_dashboard_client(tmp_path)
    try:
        clock["now"] = NOW
        for name, value, expired in [("september", period("2026-09-01", "2026-09-30"), False),
            ("old_range", period("2024-11-01", "2025-01-02"), False),
            ("expired", period("2026-10-01", "2026-10-27"), True)]:
            identifier = job(service, job_id=name, owner=user, period=value, expired=expired)
            _record_user_conversion(service, user_id=user, processing_id=identifier, created_at=NOW)
        # Upload initialization can precede the dashboard window while the
        # processing/history event belongs to today. Match identity + attempt.
        with service._connect() as conn:
            conn.execute("UPDATE conversion_jobs SET created_at=? WHERE job_id=?", ("2026-10-26T23:00:00+00:00", "september"))
            conn.commit()
        identifier = job(service, job_id="anonymous", owner="fictional-owner", kind="anonymous",
                         period=period("2026-08-03", "2026-08-12", "movement_dates"))
        _record_anonymous_conversion(service, clock, fingerprint="fictional-owner", event_id=identifier, created_at=NOW)
        _record_user_conversion(service, user_id=user, processing_id="invoice", created_at=NOW, document_type="fiscal_invoice")
        assert client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"}).status_code == 200
        response = client.get("/admin/dashboard", params={"days": 1})
        assert response.status_code == 200
        data = response.json()["statement_periods"]
        assert (data["total_count"], data["identified_count"], data["unknown_count"], data["excluded_count"]) == (4, 3, 1, 1)
        assert data["coverage_rate"] == 75
        assert (data["document_metadata_count"], data["movement_dates_count"]) == (2, 1)
        buckets = {row["key"]: row["attempts"] for row in data["age_buckets"]}
        assert buckets["previous_month"] == buckets["two_to_three_months"] == buckets["multiple_months"] == buckets["unknown"] == 1
        assert sum(buckets.values()) == 4
        ranges = {row["key"]: row for row in data["by_period"]}
        assert ranges["2026-09:2026-09"]["attempts"] == 1
        assert ranges["2024-11:2025-01"]["attempts"] == 1
        anonymous = client.get("/admin/dashboard", params={"days": 1, "identity_type": "anonymous"}).json()["statement_periods"]
        assert anonymous["total_count"] == anonymous["identified_count"] == 1
        assert anonymous["movement_dates_count"] == 1
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_missing_jobs_table_keeps_legacy_dashboard_available_with_unknown_periods(tmp_path):
    client, service, clock, user = _build_dashboard_client(tmp_path)
    try:
        clock["now"] = NOW
        _record_user_conversion(service, user_id=user, processing_id="legacy", created_at=NOW)
        client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"})
        response = client.get("/admin/dashboard", params={"days": 1})
        assert response.status_code == 200
        data = response.json()["statement_periods"]
        assert data["total_count"] == data["unknown_count"] == 1
        assert data["coverage_rate"] == 0
        assert data["by_period"][0]["key"] == "unknown"
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_job_result_cannot_be_attributed_to_another_identity(tmp_path):
    client, service, clock, user = _build_dashboard_client(tmp_path)
    try:
        clock["now"] = NOW
        identifier = job(service, job_id="different-owner", owner="other-owner", period=period("2026-09-01", "2026-09-30"))
        _record_user_conversion(service, user_id=user, processing_id=identifier, created_at=NOW)
        client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"})
        data = client.get("/admin/dashboard", params={"days": 1}).json()["statement_periods"]
        assert data["identified_count"] == 0
    finally:
        client.close()
        app.dependency_overrides.clear()


@pytest.mark.parametrize("value,expected", [(period("2026-09-01", "2026-09-30"), "current_month"),
    (period("2026-10-01", "2026-10-02"), "future"),
    (period("2025-08-01", "2025-08-30"), "older_than_twelve_months"),
    (period("2026-07-01", "2026-07-03"), "two_to_three_months"),
    (period("2026-03-01", "2026-03-30"), "four_to_twelve_months"),
    (period("2026-02-30", "2026-03-01"), "unknown"),
    (period("2026-09-30", "2026-09-01"), "unknown"),
    ({"basis": []}, "unknown"),
    (period("2026-09-01", "2026-09-30", "client_assignment"), "unknown")])
def test_period_age_uses_upload_month_in_sao_paulo_and_keeps_invalid_or_assigned_dates_unknown(value, expected):
    from app.application.admin_statement_periods import build_statement_period_summary

    events = [{"document_type": "bank_statement", "statement_period": value, "created_at": "2026-10-01T01:00:00+00:00"}]
    data = build_statement_period_summary(events)
    assert next(row for row in data["age_buckets"] if row["key"] == expected)["attempts"] == 1


def test_postgres_projects_only_period_metadata_and_respects_the_database_schema():
    from types import SimpleNamespace

    from app.application.admin_statement_periods import attach_existing_statement_periods

    queries = []
    service = SimpleNamespace(_use_postgres=True, database_schema="fixture_schema",
        _fetchone=lambda *args: {"present": "fixture_schema.conversion_jobs"},
        _fetchall=lambda conn, query, params: queries.append((query, params)) or [])
    events = [{"identity_type": "registered", "identity_id": "owner", "processing_id": "an_fixture",
               "document_type": "bank_statement"}]
    attach_existing_statement_periods(service, object(), events, end_at=NOW.isoformat())
    assert len(queries) == 1
    query, params = queries[0]
    assert '"fixture_schema"."conversion_jobs"' in query
    assert "#> '{organization_source,period}'" in query
    assert "SELECT result_payload" not in query and "preview_transactions" not in query
    assert "expires_at > ?" in query
    assert params == (NOW.isoformat(), NOW.isoformat(), "an_fixture")


def test_admin_periods_real_http_retains_authentication_and_unknown_legacy_state(tmp_path):
    import httpx

    from tests.test_admin_dashboard_http_real_api import _run_http_server

    with _run_http_server(tmp_path) as base_url, httpx.Client(base_url=base_url, timeout=10) as client:
        assert client.get("/admin/dashboard").status_code == 401
        assert client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"}).status_code == 200
        response = client.get("/admin/dashboard", params={"days": 1})
        assert response.status_code == 200
        data = response.json()["statement_periods"]
        assert data["total_count"] == data["unknown_count"] == 1
