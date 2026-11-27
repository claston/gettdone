"""Bounded regressions for the November security assessment."""
import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.application import AccessControlService
from app.application.errors import InvalidFileContentError, InvalidUserTokenError
from app.application.parsers.xlsx import parse_xlsx_transactions
from app.dependencies import get_access_control_service
from app.main import app


@pytest.fixture
def runtime(tmp_path):
    clock = {"now": datetime(2026, 11, 25, 12, tzinfo=timezone.utc)}
    settings = dict(state_file=tmp_path / "state.json", token_secret="audit-synthetic-secret",
                    now_provider=lambda: clock["now"], anonymous_quota_limit=1)
    access = AccessControlService(**settings)
    app.dependency_overrides[get_access_control_service] = lambda: access
    try:
        yield access, clock, settings
    finally:
        app.dependency_overrides.clear()
        access.close()


def legacy_token(service, user_id, version=0):
    subject = f"{user_id}:{version}" if version else user_id
    payload = base64.urlsafe_b64encode(subject.encode()).decode().rstrip("=")
    signature = hmac.new(service.token_secret, payload.encode(), hashlib.sha256).hexdigest()[:24]
    return f"{payload}.{signature}"


def test_old_credentials_are_rejected_even_with_valid_signatures_and_current_version(runtime):
    access, _, _ = runtime
    user = access.register_user("Audit", "owner@example.test", "test-password")
    old = legacy_token(access, user.user_id)
    with pytest.raises(InvalidUserTokenError):
        access.get_user_by_token(old)
    with TestClient(app) as client:
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401
        assert client.post("/auth/session/migrate", headers={"Authorization": f"Bearer {old}"}).status_code in {401, 410}
    with access._connect() as conn:
        access._execute(conn, "UPDATE users SET credential_version=7 WHERE id=?", (user.user_id,))
        conn.commit()
    with pytest.raises(InvalidUserTokenError):
        access.get_user_by_token(legacy_token(access, user.user_id, 7))


@pytest.mark.parametrize("endpoint", ["/auth/login", "/auth/register"])
def test_public_compatibility_credentials_expire_and_logout_revokes_them(runtime, endpoint):
    access, clock, _ = runtime
    if endpoint == "/auth/login":
        access.register_user("Audit", "owner@example.test", "test-password")
    payload = dict(name="Audit", email="owner@example.test", password="test-password", accepted_terms=True)
    with TestClient(app) as client:
        response = client.post(endpoint, json=payload)
        assert response.status_code == 200
        bearer = {"Authorization": "Bearer " + response.json()["user_token"]}
        assert client.get("/auth/me", headers=bearer).status_code == 200
        assert client.post("/auth/session/logout", headers=bearer).status_code == 200
        assert client.get("/auth/me", headers=bearer).status_code == 401
        new = client.post("/auth/login", json=payload).json()["user_token"]
        clock["now"] += timedelta(seconds=901)
        client.cookies.clear()
        assert client.get("/auth/me", headers={"Authorization": f"Bearer {new}"}).status_code == 401


def spreadsheet(*, last_row=2, last_column=3):
    book = Workbook()
    book.active.append(["Data", "Descrição", "Valor"])
    book.active.append(["2026-11-25", "Movimento sintético", 100])
    stream = BytesIO()
    book.save(stream)
    book.close()
    if last_row == 2 and last_column == 3:
        return stream.getvalue()
    from openpyxl.utils import get_column_letter
    output = BytesIO()
    with ZipFile(stream) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b'A1:C2', f'A1:{get_column_letter(last_column)}{last_row}'.encode())
                data = data.replace(b'<row r="2"', f'<row r="{last_row}"'.encode())
                for column in "ABC":
                    data = data.replace(f'r="{column}2"'.encode(), f'r="{column}{last_row}"'.encode())
            target.writestr(item, data)
    return output.getvalue()


@pytest.mark.parametrize("suffix", ["xml", "bin"])
def test_large_sparse_spreadsheet_is_rejected_before_grid_materialization(suffix):
    # Small enough to reproduce safely before the fix; advertised grid is 512k cells.
    raw = spreadsheet(last_row=2000, last_column=256)
    if suffix != "xml":
        output = BytesIO()
        with ZipFile(BytesIO(raw)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == "xl/worksheets/sheet1.xml":
                    data = data.replace(b"A1:IV2000", b"A1:A1").replace(b'r="C2000"', b'r="IV2000"')
                target.writestr(item.filename.replace("sheet1.xml", "sheet1.bin"),
                                data.replace(b"sheet1.xml", b"sheet1.bin"))
        raw = output.getvalue()
    with pytest.raises(InvalidFileContentError, match="limite"):
        parse_xlsx_transactions(raw)


def test_archive_expansion_is_bounded_before_openpyxl(monkeypatch):
    from app.application import xlsx_safety
    monkeypatch.setattr(xlsx_safety, "MAX_XLSX_EXPANDED_BYTES", 2048)
    with pytest.raises(InvalidFileContentError, match="limite"):
        parse_xlsx_transactions(spreadsheet())


def test_decoded_spreadsheet_text_is_bounded(monkeypatch):
    from app.application import xlsx_safety
    monkeypatch.setattr(xlsx_safety, "MAX_XLSX_TEXT_CHARACTERS", 30)
    with pytest.raises(InvalidFileContentError, match="limite"):
        parse_xlsx_transactions(spreadsheet())


def test_excessive_number_format_is_rejected_before_expansion():
    book = Workbook()
    book.active.append(["Data", "Descrição", "Valor", "Documento"])
    book.active.append(["2026-11-25", "Movimento sintético", 100, 1])
    book.active["D2"].number_format = "0" * 300
    output = BytesIO()
    book.save(output)
    book.close()
    with pytest.raises(InvalidFileContentError, match="limite"):
        parse_xlsx_transactions(output.getvalue())


def test_understated_dimensions_do_not_hide_transactions():
    output = BytesIO()
    with ZipFile(BytesIO(spreadsheet())) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet1.xml":
                data = data.replace(b'A1:C2', b'A1:A1')
            target.writestr(item, data)
    transactions = parse_xlsx_transactions(output.getvalue())
    assert len(transactions) == 1
    assert transactions[0].amount == 100
    assert transactions[0].date == "2026-11-25"


def test_anonymous_network_budget_survives_new_identity_and_service_restart(runtime):
    from app.application.public_request_budget import PublicBudgetExceeded, enforce_public_budget
    access, _, settings = runtime
    enforce_public_budget(access, kind="conversion", ip_address="192.0.2.1")
    restarted = AccessControlService(**settings)
    try:
        with pytest.raises(PublicBudgetExceeded):
            enforce_public_budget(restarted, kind="conversion", ip_address="192.0.2.1")
        enforce_public_budget(restarted, kind="conversion", ip_address="192.0.2.2")
    finally:
        restarted.close()


def test_budget_retries_are_idempotent_and_ip_values_are_not_stored(runtime):
    from app.application.public_request_budget import enforce_public_budget
    access, _, _ = runtime
    for _ in range(2):
        enforce_public_budget(access, kind="conversion", ip_address="192.0.2.1", operation_id="same-owner-batch")
    with access._connect() as conn:
        rows = access._fetchall(conn, "SELECT budget_key FROM public_request_budgets")
    assert rows and all("192.0.2.1" not in row["budget_key"] for row in rows)


def test_production_multipart_pin_includes_security_fixes():
    from pathlib import Path

    from packaging.requirements import Requirement
    from packaging.version import Version
    lines = (Path(__file__).resolve().parents[1] / "requirements-runtime.txt").read_text().splitlines()
    req = next(Requirement(line) for line in lines if line.startswith("python-multipart"))
    assert all(Version(item.version) >= Version("0.0.31") for item in req.specifier)


@pytest.mark.parametrize("path", ["//outside.invalid", "/\\outside.invalid", "/\t/outside.invalid", "https://outside.invalid"])
def test_oauth_redirects_reject_external_destinations(runtime, path):
    from app.application.google_oauth_service import GoogleOAuthService
    access, _, _ = runtime
    assert access.identity.normalize_next_path(path) == "/client-area.html"
    assert GoogleOAuthService._normalize_next_path(None, path) == "/client-area.html"
    assert access.identity.normalize_next_path("/client-area.html?tab=files#recent") == "/client-area.html?tab=files#recent"


def test_cookie_replacement_does_not_reset_http_processing_budget(runtime):
    access, _, _ = runtime
    # The first malformed document still consumes processing resources.
    with TestClient(app) as client:
        assert client.post("/auth/anonymous-session", json={}).status_code == 200
        first = client.post("/convert", files={"file": ("invalid.csv", b"invalid", "text/csv")})
        assert first.status_code == 400
        client.cookies.clear()
        assert client.post("/auth/anonymous-session", json={}).status_code == 200
        second = client.post("/convert", files={"file": ("invalid.csv", b"invalid", "text/csv")})
        assert second.status_code == 429
        assert second.json()["detail"]["code"] == "public_request_limit"
        assert int(second.headers["Retry-After"]) > 0


def test_public_budget_is_atomic_across_instances(runtime):
    from concurrent.futures import ThreadPoolExecutor

    from app.application.public_request_budget import PublicBudgetExceeded, enforce_public_budget
    access, _, settings = runtime
    other = AccessControlService(**settings)
    def attempt(index):
        try:
            enforce_public_budget((access, other)[index % 2], kind="conversion", ip_address="192.0.2.1")
            return True
        except PublicBudgetExceeded:
            return False
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert sum(pool.map(attempt, range(8))) == 1
    finally:
        other.close()


def test_ipv6_addresses_within_same_network_share_budget(runtime):
    from app.application.public_request_budget import PublicBudgetExceeded, enforce_public_budget
    access, _, _ = runtime
    enforce_public_budget(access, kind="conversion", ip_address="2001:db8:1:2::1")
    with pytest.raises(PublicBudgetExceeded):
        enforce_public_budget(access, kind="conversion", ip_address="2001:db8:1:2::2")
    enforce_public_budget(access, kind="conversion", ip_address="2001:db8:1:3::1")
