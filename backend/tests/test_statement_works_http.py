import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.application.conversion.contracts.jobs import ConversionJobResultReference
from app.dependencies import get_access_control_service, get_conversion_batch_service
from app.main import app
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server

pytestmark = pytest.mark.real_http


def test_work_lifecycle_snapshot_reassessment_permissions_and_quota(tmp_path, monkeypatch):
    monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "true")
    monkeypatch.setenv("PRO_STATEMENT_WORKS_ENABLED", "true")
    with _run_pro_upload_http_server(tmp_path) as (base, publisher, token):
        access = app.dependency_overrides[get_access_control_service]()
        repository = app.dependency_overrides[get_conversion_batch_service]().repository
        identity = access.resolve_identity(anonymous_fingerprint=None, user_token=token)
        quota = access.get_remaining_quota(identity)
        headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
        with httpx.Client(base_url=base, headers=headers) as client:
            assert httpx.get(base + "/api/pro/works").status_code == 401
            customer = client.post("/api/pro/organization/clients", json={"name": "Aurora"}).json()
            account = client.post("/api/pro/organization/accounts", json={"name": "Principal", "client_id": customer["id"]}).json()
            period = client.post(
                "/api/pro/organization/periods", json={"name": "Ciclo", "start_date": "2026-09-20", "end_date": "2026-10-19"}
            ).json()
            request = {"client_id": customer["id"], "period_id": period["id"]}
            work = client.post("/api/pro/works", json=request)
            assert work.status_code == 200, work.text
            work = work.json()
            work_id = work["id"]
            path = f"/api/pro/works/{work_id}"
            assert client.post("/api/pro/works", json=request).json()["id"] == work_id
            assert client.post("/api/pro/works", json={**request, "owner_user_id": "foreign"}).status_code == 400
            assert httpx.post(base + "/api/pro/works", headers={"Authorization": f"Bearer {token}"}, json=request).status_code == 403
            assert (
                client.post(path + "/complete", json={"expected_version": 1, "expected_fingerprint": work["fingerprint"]}).status_code
                == 409
            )
            work = client.put(path + "/scope", json={"expected_version": work["version"], "account_ids": [account["id"]]}).json()
            assert client.put(path + "/scope", json={"expected_version": 1, "account_ids": [account["id"]]}).status_code == 409
            assert client.get(path).json()["version"] == work["version"]  # failed mutation rolled back
            raw = b"date,amount,description\n2026-10-01,12,PIX\n"
            batch = client.post(
                "/api/pro/conversion-batches",
                headers={"Idempotency-Key": "ofx61-http"},
                json={
                    "files": [
                        {
                            "filename": "extrato.csv",
                            "content_type": "text/csv",
                            "size_bytes": len(raw),
                            "sha256_hex": hashlib.sha256(raw).hexdigest(),
                        }
                    ]
                },
            ).json()
            job = batch["jobs"][0]["job_id"]
            repository.mark_uploaded(job)
            repository.mark_queued(job)
            repository.mark_running(job)
            analysis = {
                "updated_at": "2026-10-27T12:00:00Z",
                "opening_balance": 0,
                "closing_balance": 112,
                "preview_transactions": [
                    {"transaction_id": "outside", "date": "2026-09-01", "description": "Fora", "amount": 100},
                    {"transaction_id": "inside", "date": "2026-10-01", "description": "=PIX", "amount": 12},
                ],
            }
            repository.mark_completed(
                job, result=ConversionJobResultReference(analysis_id="an_work", payload={"analysis": analysis, "reviewed_at": "reviewed"})
            )
            auto = client.post(path + f"/batches/{batch['batch_id']}", json={"expected_version": work["version"]})
            assert auto.status_code == 200, auto.text
            work = auto.json()
            assert work["documents"][0]["context_version"] == 1
            linked = client.post(
                path + "/documents",
                json={"expected_version": work["version"], "job_id": job, "account_id": account["id"], "context_version": 1},
            )
            assert linked.status_code == 200, linked.text
            work = linked.json()
            assert work["documents"][0]["review_url"].endswith(f"return_to=%2Fworks%2F{work_id}")
            work = client.put(
                path + f"/documents/{job}/evidence",
                json={
                    "expected_version": work["version"],
                    "identity_confirmed": True,
                    "start_date": "2026-09-01",
                    "end_date": "2026-10-31",
                },
            ).json()
            assert work["assessment"]["readiness"] == "ready", work
            done = client.post(path + "/complete", json={"expected_version": work["version"], "expected_fingerprint": work["fingerprint"]})
            assert done.status_code == 200, done.text
            done = done.json()
            assert done["state"] == "completed" and not done["needs_reassessment"]
            client.post(
                "/api/pro/organization/accounts", json={"name": "Conta fora do escopo", "client_id": customer["id"]}
            ).raise_for_status()
            assert not client.get(path).json()["needs_reassessment"]
            snapshot = done["closings"][0]
            assert "=PIX" not in str(snapshot), "Closure snapshot must not retain financial rows past result TTL"
            exported = client.get(path + "/export")
            assert exported.status_code == 200 and "'=PIX" in exported.text and "Fora" not in exported.text
            assert (
                client.put(path + "/scope", json={"expected_version": done["version"], "account_ids": [account["id"]]}).status_code == 409
            )
            repository.update_analysis(job, analysis={**analysis, "updated_at": "2026-10-27T13:00:00Z"})
            changed = client.get(path).json()
            assert changed["needs_reassessment"]
            assert changed["closings"][0] == snapshot
            assert client.get(path + "/export").status_code == 409
            opened = client.post(path + "/reopen", json={"expected_version": done["version"], "reason": "Conferir correção"}).json()
            assert opened["state"] == "open" and opened["closings"][0] == snapshot
            repository.mark_reviewed(job, reviewed_at="reviewed-again")
            current = client.get(path).json()
            stale = client.post(
                path + "/complete", json={"expected_version": current["version"], "expected_fingerprint": done["fingerprint"]}
            )
            assert stale.status_code == 409
            second = client.post(
                path + "/complete", json={"expected_version": current["version"], "expected_fingerprint": current["fingerprint"]}
            )
            assert second.status_code == 200, second.text
            assert len(second.json()["closings"]) == 2
            assert second.json()["closings"][1] == snapshot
            other = access.register_user(name="Other", email="other-ofx61@example.test", password="strong-password")
            access.activate_user_plan(user_id=other.user_id, plan_code="essencial")
            assert client.get(path, headers={"Authorization": f"Bearer {other.token}"}).status_code == 404
            assert client.get("/api/pro/works", headers={"Authorization": f"Bearer {other.token}"}).json()["items"] == []
            second_period = client.post(
                "/api/pro/organization/periods",
                json={
                    "name": "Semana",
                    "start_date": "2026-10-01",
                    "end_date": "2026-10-07",
                },
            ).json()
            reused = client.post("/api/pro/works", json={"client_id": customer["id"], "period_id": second_period["id"]}).json()
            reused_path = f"/api/pro/works/{reused['id']}"
            reused = client.put(reused_path + "/scope", json={"expected_version": reused["version"], "account_ids": [account["id"]]}).json()
            reused = client.post(
                reused_path + "/documents",
                json={
                    "expected_version": reused["version"],
                    "job_id": job,
                    "account_id": account["id"],
                    "context_version": second.json()["documents"][0]["context_version"],
                },
            ).json()
            reused = client.put(
                reused_path + f"/documents/{job}/evidence",
                json={
                    "expected_version": reused["version"],
                    "identity_confirmed": True,
                },
            ).json()
            completion = {"expected_version": reused["version"], "expected_fingerprint": reused["fingerprint"]}
            assert client.post(reused_path + "/complete", json=completion).status_code == 400
            partial = client.post(
                reused_path + "/complete", json={**completion, "remarks": {account["id"]: "Intervalo documental a confirmar no original"}}
            )
            assert partial.status_code == 200, partial.text
            assert partial.json()["closings"][0]["snapshot"]["conclusion"] == "with_reservations"
            assert partial.json()["assessment"]["movements_count"] == 1
            assert client.get(path).json()["closings"][1] == snapshot
            record = repository.get(job)
            repository._jobs[job] = replace(record, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            expired = client.get(path).json()
            assert expired["needs_reassessment"] and not expired["assessment"]["movements"]
            assert expired["documents"][0]["review_url"] is None
            assert client.get(path + "/export").status_code == 409
            assert access.get_remaining_quota(identity) == quota and publisher.calls == []
            monkeypatch.setenv("PRO_STATEMENT_WORKS_ENABLED", "false")
            assert client.get(path).status_code == 503
            assert client.get("/api/pro/conversions").status_code == 200
