import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app.application import AccessControlService, ReportService, TempAnalysisStorage
from app.application.errors import InvalidUserTokenError
from app.application.export_artifact_service import ExportArtifactService
from app.application.models import AnalysisData, TransactionRow
from app.dependencies import get_access_control_service, get_analysis_repository, get_report_service
from app.main import app
from app.routers.auth import SESSION_ACCESS_COOKIE_NAME, SESSION_REFRESH_COOKIE_NAME
from tests.admin_setup import register_admin


@pytest.fixture
def runtime(tmp_path):
    clock = {"now": datetime(2026, 11, 18, 12, tzinfo=timezone.utc)}
    settings = dict(state_file=tmp_path / "state.json", token_secret="test-secret", now_provider=lambda: clock["now"])
    access = AccessControlService(**settings)
    storage = TempAnalysisStorage(root_dir=tmp_path / "reports")
    app.dependency_overrides[get_access_control_service] = lambda: access
    app.dependency_overrides[get_report_service] = lambda: ReportService(storage)
    app.dependency_overrides[get_analysis_repository] = lambda: storage
    try:
        yield access, storage, clock, settings
    finally:
        app.dependency_overrides.clear()
        access.close()


def test_admin_logout_revokes_replay_but_preserves_another_session(runtime):
    access, _, _, _ = runtime
    admin = register_admin(access, name="Admin", email="admin@example.test", password="test-password")
    other = access.create_user_session(user_id=admin.user_id)
    with TestClient(app) as client:
        assert client.post("/admin/auth/login", json={"email": admin.email, "password": "test-password"}).status_code == 200
        token, refresh = client.cookies[SESSION_ACCESS_COOKIE_NAME], client.cookies[SESSION_REFRESH_COOKIE_NAME]
        assert client.post("/admin/auth/logout").status_code == 200
        assert client.get("/admin/me", headers={"Cookie": f"{SESSION_ACCESS_COOKIE_NAME}={token}"}).status_code == 401
        assert client.post("/auth/session/refresh", headers={"Cookie": f"{SESSION_REFRESH_COOKIE_NAME}={refresh}"}).status_code == 401
    assert access.get_user_by_session_access_token(other.access_token).user_id == admin.user_id


@pytest.mark.real_http
@pytest.mark.parametrize("logout_path", ["/auth/session/logout", "/admin/auth/logout"])
def test_logout_revokes_rotated_family_over_real_http(runtime, logout_path):
    from tests.http_runtime import LoopbackClient, serve_http

    access, _, _, _ = runtime
    admin = register_admin(access, name="Admin", email="rotated@example.test", password="test-password")
    independent = access.create_user_session(user_id=admin.user_id)
    with serve_http(app) as url, LoopbackClient(base_url=url) as original, LoopbackClient(base_url=url) as copied:
        assert original.post("/admin/auth/login", json={"email": admin.email, "password": "test-password"}).status_code == 200
        old_access = original.cookies[SESSION_ACCESS_COOKIE_NAME]
        old_refresh = original.cookies[SESSION_REFRESH_COOKIE_NAME]
        response = copied.post("/auth/session/refresh", headers={"Cookie": f"{SESSION_REFRESH_COOKIE_NAME}={old_refresh}"})
        assert response.status_code == 200
        assert copied.get("/admin/me").status_code == 200
        assert original.post(logout_path).status_code == 200
        assert copied.get("/admin/me").status_code == 401
        assert copied.post("/auth/session/refresh").status_code == 401
        assert original.get("/admin/me", headers={"Cookie": f"{SESSION_ACCESS_COOKIE_NAME}={old_access}"}).status_code == 401
        assert original.post("/auth/session/refresh", headers={"Cookie": f"{SESSION_REFRESH_COOKIE_NAME}={old_refresh}"}).status_code == 401
        assert original.post(logout_path).status_code == 200
    assert access.get_user_by_session_access_token(independent.access_token).user_id == admin.user_id
    renewed = access.refresh_user_session(refresh_token=independent.refresh_token)
    assert access.get_user_by_session_access_token(renewed.access_token).user_id == admin.user_id


def test_logout_all_also_invalidates_legacy_bearer_tokens(runtime):
    access, _, _, _ = runtime
    user = access.register_user("Owner", "owner@example.test", "test-password")
    with TestClient(app) as client:
        assert client.post("/auth/session/logout-all", headers={"Authorization": f"Bearer {user.token}"}).status_code == 200
    with pytest.raises(InvalidUserTokenError):
        access.get_user_by_token(user.token)
    assert access.authenticate_user(user.email, "test-password").user_id == user.user_id


def test_password_login_budget_is_shared_across_routes_instances_and_survives_restart(runtime):
    access, _, clock, settings = runtime
    user = access.register_user("Owner", "owner@example.test", "test-password")
    routes = ("/auth/login", "/auth/session/login", "/admin/auth/login", "/auth/pro/session/login")
    headers = {"Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
    with TestClient(app) as client:
        for i in range(10):
            result = client.post(routes[i % len(routes)], json={"email": user.email.upper(), "password": "wrong"}, headers=headers)
            assert result.status_code == 401
        restarted = AccessControlService(**settings)
        app.dependency_overrides[get_access_control_service] = lambda: restarted
        try:
            result = client.post("/auth/session/login", json={"email": user.email, "password": "test-password"})
            assert result.status_code == 429
            assert 1 <= int(result.headers["retry-after"]) <= 300
            clock["now"] += timedelta(seconds=301)
            assert client.post("/auth/session/login", json={"email": user.email, "password": "test-password"}).status_code == 200
        finally:
            restarted.close()


def test_ip_budget_limits_password_spray_and_ignores_forwarded_header(runtime):
    with TestClient(app) as client:
        for i in range(60):
            assert client.post("/auth/login", json={"email": f"missing{i}@example.test", "password": "wrong"}).status_code == 401
        result = client.post("/auth/login", json={"email": "another@example.test", "password": "wrong"},
                             headers={"X-Forwarded-For": "192.0.2.123"})
        assert result.status_code == 429


@pytest.mark.parametrize("value", ["=1+1", "+SUM(A1:A2)", "-cmd", "@SUM(A1:A2)", " \t=1+1", "\r=1+1"])
@pytest.mark.parametrize("multiple_accounts", [False, True])
def test_export_preserves_text_and_numbers_without_executing_formulas(tmp_path, value, multiple_accounts):
    row = TransactionRow("2026-11-18", value, -100.0, value, "unmatched", document_number=value)
    if multiple_accounts:
        row.statement_account = dict(account_id="account", block_id="block", bank_code="001", bank_branch="0001",
                                     account_number="12345", period_start="2026-11-01", period_end="2026-11-30")
    ExportArtifactService().write_convert_artifacts(tmp_path, report_rows=[row])
    workbook = load_workbook(tmp_path / "converted.xlsx", data_only=False)
    # XML normalizes literal carriage returns to line feeds.
    assert workbook.active["B2"].value == value.replace("\r", "\n")
    assert workbook.active["B2"].data_type == "s"
    assert workbook.active["D2"].value == 100
    workbook.close()
    with (tmp_path / "converted.csv").open(encoding="utf-8", newline="") as handle:
        exported = list(csv.DictReader(handle))[0]
    assert exported["description"] == "'" + value
    assert exported["category"] == "'" + value
    assert exported["document_number"] == "'" + value
    assert float(exported["amount"]) == -100
    assert row.description == value


def test_unowned_reports_are_denied_until_explicit_owner_is_assigned(runtime):
    access, storage, _, _ = runtime
    data = AnalysisData("an_security", "csv", "test.csv", 1, 100, 0, 100,
                        [TransactionRow("2026-11-18", "Synthetic", 100, "other", "unmatched")])
    storage.save_analysis(data)
    reconcile_id, _ = storage.save_reconcile_report({}, [], [])
    with TestClient(app) as client:
        assert client.post("/auth/anonymous-session", json={}).status_code == 200
        for path in ("/report/an_security", "/convert-report/an_security", f"/reconcile-report/{reconcile_id}"):
            assert client.get(path).status_code == 403
        user = access.register_user("Owner", "owner@example.test", "test-password")
        storage.set_report_owner("an_security", "user", user.user_id)
        storage.set_convert_owner("an_security", "user", user.user_id)
        storage.set_reconcile_owner(reconcile_id, "user", user.user_id)
        for path in ("/report/an_security", "/convert-report/an_security", f"/reconcile-report/{reconcile_id}"):
            assert client.get(path, headers={"Authorization": f"Bearer {user.token}"}).status_code == 200


def test_reconciliation_exports_escape_only_text(runtime):
    _, storage, _, _ = runtime
    identifier, _ = storage.save_reconcile_report({}, [dict(row_id="row", source="bank", date="2026-11-18",
        description="=1+1", amount=-100.0, status="pendente", reason="=2+2")], [])
    book = load_workbook(storage.get_reconcile_report_path(identifier, "xlsx"), data_only=False)
    assert book["Conciliacao_Detalhada"]["D2"].data_type == "s"
    assert book["Problemas"]["D2"].data_type == "s"
    book.close()
    with storage.get_reconcile_report_path(identifier, "csv").open(encoding="utf-8", newline="") as handle:
        row = list(csv.reader(handle))[1]
    assert row[3] == "'=1+1"
    assert float(row[4]) == -100


@pytest.mark.real_http
def test_security_controls_over_real_http(runtime):
    from tests.http_runtime import LoopbackClient, serve_http

    access, _, _, _ = runtime
    admin = register_admin(access, name="Admin", email="http-admin@example.test", password="test-password")
    with serve_http(app) as base_url, LoopbackClient(base_url=base_url) as client:
        assert client.post("/auth/anonymous-session", json={}).status_code == 200
        result = client.post("/convert", files={"file": ("bank.csv", b"date,description,amount\n2026-11-18,=1+1,-100\n", "text/csv")})
        assert result.status_code == 200, result.text
        identifier = result.json()["analysis"]["analysis_id"]
        edited = client.post(f"/convert-edits/{identifier}", json={"edits": [
            {"row_id": "row_1", "date": "2026-11-18", "description": "=1+1", "credit": None, "debit": 100}]})
        assert edited.status_code == 200, edited.text
        report = client.get(f"/convert-report/{identifier}?format=xlsx")
        assert report.status_code == 200
        workbook = load_workbook(BytesIO(report.content), data_only=False)
        assert workbook.active["B2"].value == "=1+1"
        assert workbook.active["B2"].data_type == "s"
        workbook.close()
        assert client.get("/report/does-not-exist").status_code == 404
        for _ in range(10):
            assert client.post("/auth/login", json={"email": "missing@example.test", "password": "wrong"}).status_code == 401
        assert client.post("/auth/session/login", json={"email": "missing@example.test", "password": "wrong"}).status_code == 429
        assert client.post("/admin/auth/login", json={"email": admin.email, "password": "test-password"}).status_code == 200
        refresh = client.cookies[SESSION_REFRESH_COOKIE_NAME]
        assert client.post("/auth/session/logout").status_code == 200
        assert client.post("/auth/session/refresh", headers={"Cookie": f"{SESSION_REFRESH_COOKIE_NAME}={refresh}"}).status_code == 401
