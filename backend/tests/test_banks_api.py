from fastapi.testclient import TestClient

from app.main import app


def test_banks_endpoint_returns_bank_list() -> None:
    client = TestClient(app)

    response = client.get("/banks")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("banks"), list)
    assert len(payload["banks"]) > 0
    first = payload["banks"][0]
    assert "code" in first
    assert "label" in first
