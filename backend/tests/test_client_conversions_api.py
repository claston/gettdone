from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.application import AccessControlService
from app.dependencies import get_access_control_service
from app.main import app


def build_client(tmp_path: Path) -> tuple[TestClient, str, str]:
    current = {"now": datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)}
    access_control = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        now_provider=lambda: current["now"],
    )

    user_a = access_control.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    user_b = access_control.register_user(name="Clara", email="clara@example.com", password="strong-pass")

    access_control.record_user_conversion(
        user_id=user_a.user_id,
        processing_id="an_old",
        filename="NU_150702837_01NOV2023_30NOV2023.pdf",
        model="Nubank",
        conversion_type="pdf-ofx",
        status="Sucesso",
        transactions_count=7,
        created_at=current["now"].isoformat(),
    )

    current["now"] = current["now"] + timedelta(minutes=5)
    access_control.record_user_conversion(
        user_id=user_a.user_id,
        processing_id="an_new",
        filename="itau_extrato_032026.pdf",
        model="Itau",
        conversion_type="pdf-ofx",
        status="Sucesso",
        transactions_count=11,
        created_at=current["now"].isoformat(),
    )

    current["now"] = current["now"] + timedelta(minutes=1)
    access_control.record_user_conversion(
        user_id=user_b.user_id,
        processing_id="an_other",
        filename="other_user.pdf",
        model="Nubank",
        conversion_type="pdf-ofx",
        status="Sucesso",
        transactions_count=3,
        created_at=current["now"].isoformat(),
    )

    app.dependency_overrides[get_access_control_service] = lambda: access_control
    return TestClient(app), user_a.token, user_b.token


def test_client_conversions_returns_only_owner_items_in_desc_order(tmp_path: Path) -> None:
    client, token_a, _token_b = build_client(tmp_path)

    response = client.get("/client/conversions", params={"user_token": token_a})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 2
    assert payload["items"][0]["processing_id"] == "an_new"
    assert payload["items"][0]["filename"] == "itau_extrato_032026.pdf"
    assert payload["items"][0]["model"] == "Itau"
    assert payload["items"][0]["conversion_type"] == "pdf-ofx"
    assert payload["items"][0]["status"] == "Sucesso"
    assert payload["items"][0]["transactions_count"] == 11
    assert payload["items"][1]["processing_id"] == "an_old"
    app.dependency_overrides.clear()


def test_client_conversions_respects_limit_parameter(tmp_path: Path) -> None:
    client, token_a, _token_b = build_client(tmp_path)

    response = client.get("/client/conversions", params={"user_token": token_a, "limit": 1})

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1
    assert payload["items"][0]["processing_id"] == "an_new"
    app.dependency_overrides.clear()


def test_client_conversions_rejects_invalid_token(tmp_path: Path) -> None:
    client, _token_a, _token_b = build_client(tmp_path)

    response = client.get("/client/conversions", params={"user_token": "invalid-token"})

    assert response.status_code == 401
    assert "Invalid user token" in response.json()["detail"]
    app.dependency_overrides.clear()
