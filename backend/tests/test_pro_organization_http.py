import hashlib

import httpx
import pytest

from app.dependencies import get_access_control_service
from app.main import app
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server

pytestmark = pytest.mark.real_http


def test_http_manual_organization_filters_permissions_and_no_processing(tmp_path, monkeypatch):
    monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "true")
    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        access = app.dependency_overrides[get_access_control_service]()
        identity = access.resolve_identity(anonymous_fingerprint=None, user_token=token)
        initial_quota = access.get_remaining_quota(identity)
        free = access.register_user(name="Free", email="free@example.test", password="strong-password")
        other = access.register_user(name="Other", email="other@example.test", password="strong-password")
        access.activate_user_plan(user_id=other.user_id, plan_code="essencial")
        headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
        with httpx.Client(base_url=base_url, headers=headers) as client:
            assert httpx.get(f"{base_url}/api/pro/organization/catalog").status_code == 401
            assert client.get("/api/pro/organization/catalog", headers={"Authorization": f"Bearer {free.token}"}).status_code == 403
            assert client.get("/api/pro/organization/catalog").json() == {"clients": [], "accounts": [], "periods": []}
            forbidden = httpx.post(
                f"{base_url}/api/pro/organization/clients", headers={"Authorization": f"Bearer {token}"}, json={"name": "Missing guard"}
            )
            assert forbidden.status_code == 403
            malicious = client.post("/api/pro/organization/clients", json={"name": "A", "owner_user_id": other.user_id})
            assert malicious.status_code == 400
            created = client.post("/api/pro/organization/clients", json={"name": "Cliente A", "tax_id": "12.345.678/0001-90"})
            assert created.status_code == 201, created.text
            client_id = created.json()["id"]
            assert "organization_id" not in created.json() and "created_by" not in created.json()
            account = client.post(
                "/api/pro/organization/accounts", json={"client_id": client_id, "name": "Conta A", "account_number": "000123-4"}
            )
            assert account.status_code == 201, account.text
            period_ids = []
            for name, start, end in [("Quinzena", "2026-09-20", "2026-10-04"), ("Semana", "2026-10-01", "2026-10-07")]:
                period = client.post("/api/pro/organization/periods", json={"name": name, "start_date": start, "end_date": end})
                assert period.status_code == 201, period.text
                period_ids.append(period.json()["id"])
            assert (
                client.post(
                    "/api/pro/organization/periods", json={"name": "Invalid", "start_date": "2026-10-02", "end_date": "2026-10-01"}
                ).status_code
                == 400
            )
            assert client.patch(f"/api/pro/organization/clients/{client_id}", json={"name": "Cliente corrigido"}).status_code == 200
            raw = b"date,amount,description\n2026-10-01,12.00,PIX\n"
            created_batch = client.post(
                "/api/pro/conversion-batches",
                headers={"Idempotency-Key": "organization-http"},
                json={
                    "files": [
                        {
                            "filename": f"{n}.csv",
                            "content_type": "text/csv",
                            "size_bytes": len(raw),
                            "sha256_hex": hashlib.sha256(raw).hexdigest(),
                        }
                        for n in range(2)
                    ],
                },
            )
            assert created_batch.status_code == 201, created_batch.text
            batch_id = created_batch.json()["batch_id"]
            jobs = created_batch.json()["jobs"]
            context_url = f"/api/pro/organization/batches/{batch_id}/jobs/{jobs[0]['job_id']}/context"
            updated = client.put(context_url, json={"client_id": client_id, "account_id": account.json()["id"], "period_ids": period_ids})
            assert updated.status_code == 200, updated.text
            assert updated.json()["version"] == 1
            assert client.put(context_url, json={"period_ids": []}).status_code == 409
            other_job_url = f"/api/pro/organization/batches/{batch_id}/jobs/{jobs[1]['job_id']}/context"
            assert client.put(other_job_url, json={"period_ids": [period_ids[1]]}).status_code == 200
            all_rows = client.get("/api/pro/organization/conversions")
            assert all_rows.status_code == 200, all_rows.text
            assert len(all_rows.json()["items"]) == 2
            first_page = client.get("/api/pro/organization/conversions", params={"limit": 1}).json()
            assert len(first_page["items"]) == 1 and first_page["next_cursor"]
            second_page = client.get("/api/pro/organization/conversions", params={"limit": 1, "cursor": first_page["next_cursor"]}).json()
            assert len(second_page["items"]) == 1 and second_page["next_cursor"] is None
            assert first_page["items"][0]["job_id"] != second_page["items"][0]["job_id"]
            assert "no-store" in all_rows.headers["cache-control"]
            assert all(row["review_url"].startswith("/review/") for row in all_rows.json()["items"])
            assert len(client.get("/api/pro/organization/conversions", params={"unassigned": "true"}).json()["items"]) == 1
            assert len(client.get("/api/pro/organization/conversions", params={"period_id": period_ids[1]}).json()["items"]) == 2
            combined = client.get("/api/pro/organization/conversions", params={"period_id": period_ids[0], "client_id": client_id})
            assert len(combined.json()["items"]) == 1
            assert client.get("/api/pro/organization/conversions", params={"cursor": "invalid"}).status_code == 400
            assert client.put(context_url, headers={"Authorization": f"Bearer {other.token}"}, json={}).status_code == 404
            assert (
                client.patch(
                    f"/api/pro/organization/clients/{client_id}",
                    headers={"Authorization": f"Bearer {other.token}"},
                    json={"name": "Foreign"},
                ).status_code
                == 404
            )
            assert client.get("/api/pro/organization/conversions", headers={"Authorization": f"Bearer {other.token}"}).json()["items"] == []
            assert client.get("/api/pro/organization/catalog", headers={"Authorization": f"Bearer {other.token}"}).json()["clients"] == []
            cleared = client.put(context_url, json={"expected_version": 1})
            assert cleared.status_code == 200 and cleared.json()["period_ids"] == []
            assert access.get_remaining_quota(identity) == initial_quota
            assert publisher.calls == []
            monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "false")
            assert client.get("/api/pro/organization/catalog").status_code == 503
