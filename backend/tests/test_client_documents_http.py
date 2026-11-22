import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.access_control import AccessControlService
from app.application.conversion.contracts.batches import ConversionFileSummary
from app.application.conversion.contracts.jobs import ConversionJobRecord, ConversionJobResultReference
from app.application.pro_organization import OrganizationService
from app.dependencies import get_access_control_service, get_conversion_batch_service
from app.main import app
from app.routers.pro_documents import document_category, documents
from tests.http_runtime import LoopbackClient
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server

pytestmark = pytest.mark.real_http


def test_expired_imports_are_hidden_for_all_clients_without_deleting_records(tmp_path, monkeypatch):
    from app.application.conversion.contracts.jobs import ConversionJobStatus

    monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "true")
    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        repository = app.dependency_overrides[get_conversion_batch_service]().repository
        headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
        with LoopbackClient(base_url=base_url, headers=headers) as client:
            customers = [client.post("/api/pro/organization/clients", json={"name": name}).json()["id"]
                         for name in ("Cliente A", "Cliente B")]
            live_ids = []
            for index, customer in enumerate([*customers, None, None]):
                result = client.post("/api/pro/conversion-batches", headers={"Idempotency-Key": f"expiry-{index}"},
                    json={"files": [{"filename": f"{kind}.csv", "content_type": "text/csv", "size_bytes": 20,
                                     "sha256_hex": "0" * 64} for kind in ("disponivel", "prazo", "estado")]})
                assert result.status_code == 201, result.text
                batch = result.json()
                for position, job in enumerate(batch["jobs"]):
                    job_id = job["job_id"]
                    if customer:
                        saved = client.put(f"/api/pro/organization/batches/{batch['batch_id']}/jobs/{job_id}/context",
                                           json={"client_id": customer})
                        assert saved.status_code == 200, saved.text
                    record = repository.get(job_id)
                    if position == 1 or index == 3:
                        repository._jobs[job_id] = replace(record, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
                    elif position == 2:
                        repository._jobs[job_id] = replace(record, status=ConversionJobStatus.EXPIRED)
                    else:
                        repository._jobs[job_id] = replace(record, expires_at=datetime.now(timezone.utc) + timedelta(minutes=5))
                        live_ids.append(job_id)
            for path in ("/api/pro/documents", "/api/pro/organization/conversions", "/api/pro/conversions"):
                response = client.get(path, params={"limit": 2})
                assert response.status_code == 200, response.text
                first = response.json()
                assert len(first["items"]) == 2 and first["next_cursor"]
                last = client.get(path, params={"limit": 2, "cursor": first["next_cursor"]}).json()
                assert len(last["items"]) == 1 and last["next_cursor"] is None
                assert {row["job_id"] for row in first["items"] + last["items"]} == set(live_ids)
            for customer, job_id in zip(customers, live_ids):
                rows = client.get("/api/pro/documents", params={"client_id": customer}).json()["items"]
                assert [row["job_id"] for row in rows] == [job_id]
            batches = client.get("/api/pro/conversion-batches").json()
            assert len(batches["items"]) == 3 and batches["next_cursor"] is None
            assert all(not row["is_expired"] for row in batches["items"])
            for row in batches["items"]:
                assert datetime.fromisoformat(row["expires_at"]) == repository.get(row["first_job_id"]).expires_at
            assert len(repository._jobs) == 12 and len(repository._batches) == 4
            assert publisher.calls == []


def test_documents_inventory_is_scoped_paginated_and_does_not_assume_pdf_is_statement(tmp_path, monkeypatch):
    monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "true")
    with _run_pro_upload_http_server(tmp_path) as (base_url, publisher, token):
        headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
        with LoopbackClient(base_url=base_url, headers=headers) as client:
            customer = client.post("/api/pro/organization/clients", json={"name": "Sem extratos"}).json()
            assert client.get("/api/pro/documents", params={"client_id": customer["id"]}).json()["items"] == []
            raw = b"date,amount,description\n2026-10-01,12.00,PIX\n"
            created = client.post("/api/pro/conversion-batches", headers={"Idempotency-Key": "documents-inventory"}, json={"files": [
                {"filename": "arquivo.pdf", "content_type": "application/pdf", "size_bytes": len(raw),
                 "sha256_hex": hashlib.sha256(raw).hexdigest()}
            ]})
            assert created.status_code == 201, created.text
            batch = created.json()
            result = client.get("/api/pro/documents", params={"unassigned": "true"})
            assert result.status_code == 200, result.text
            item = result.json()["items"][0]
            assert item["category"] == "unclassified"
            assert "result" not in item and item["expires_at"]
            assert client.get("/api/pro/documents", params={"category": "statements"}).json()["items"] == []
            scoped = client.get("/api/pro/documents", params={"batch_id": batch["batch_id"]}).json()
            assert len(scoped["items"]) == 1
            assert client.get("/api/pro/documents", params={"client_id": "client_foreign"}).status_code == 404
            assert client.get("/api/pro/documents", params={"client_id": customer["id"], "unassigned": "true"}).status_code == 400
            assert client.get("/api/pro/documents", params={"cursor": "invalid"}).status_code == 400
            access = app.dependency_overrides[get_access_control_service]()
            other = access.register_user(name="Outro", email="documents-other@example.test", password="strong-password")
            access.activate_user_plan(user_id=other.user_id, plan_code="essencial")
            other_headers = {"Authorization": f"Bearer {other.token}"}
            assert client.get("/api/pro/documents", headers=other_headers).json()["items"] == []
            assert client.get("/api/pro/documents", headers=other_headers, params={"batch_id": batch["batch_id"]}).status_code == 404
            assert publisher.calls == []


@pytest.mark.parametrize("kind,category", [("bank_statement", "statements"), ("bank_receipt", "receipts"),
    ("notary_receipt", "receipts"), ("fiscal_invoice", "invoices"), ("financial_spreadsheet", "unclassified"),
    (None, "unclassified")])
def test_document_categories_use_extracted_type(kind, category):
    assert document_category({"analysis": {"document_type": kind}}) == category


def test_document_inventory_bounded_sparse_pagination_and_expiration(tmp_path):
    access = AccessControlService(state_file=tmp_path / "access.json", token_secret="inventory-test")
    owner = access.register_user(name="Owner", email="inventory@example.test", password="long-password").user_id
    now = datetime.now(timezone.utc)
    rows = [ConversionFileSummary(f"job_{index:03d}", "batch_a", "arquivo.pdf", "completed",
            now - timedelta(seconds=index), now + timedelta(hours=1)) for index in range(101)]
    calls = []

    def listed(**kwargs):
        assert kwargs["identity_id"] == owner
        return tuple(row for row in rows if kwargs["before"] is None or (row.created_at, row.job_id) < kwargs["before"])[:kwargs["limit"]]

    def get(job_id):
        calls.append(job_id)
        return ConversionJobRecord(job=SimpleNamespace(job_id=job_id,
            identity=SimpleNamespace(identity_type="user", identity_id=owner), document=SimpleNamespace(sha256_hex="0" * 64)),
            expires_at=now + timedelta(hours=1), status="completed", created_at=now, updated_at=now,
            result=ConversionJobResultReference(analysis_id="inventory", payload={"analysis": {
                "document_type": "bank_receipt" if job_id == "job_100" else "unknown"},
                                           "private_financial_rows": "must not be returned"}))

    args = dict(owner=owner, service=OrganizationService(access.db), report=None, async_report=None,
        batches=SimpleNamespace(repository=SimpleNamespace(list_files_for_owner=listed, get=get)),
        client_id=None, batch_id=None, unassigned=False, category="receipts", cursor=None, limit=30)
    first = documents(**args)
    assert first["items"] == [] and first["next_cursor"] and len(calls) == 100
    second = documents(**{**args, "cursor": first["next_cursor"]})
    assert [item["job_id"] for item in second["items"]] == ["job_100"]
    assert second["next_cursor"] is None and "private_financial_rows" not in str(second)
    rows[:] = [ConversionFileSummary("job_old", "batch_a", "arquivo.pdf", "completed", now - timedelta(days=2), now - timedelta(days=1))]
    calls.clear()
    expired = documents(**{**args, "category": "all"})
    assert expired == {"items": [], "next_cursor": None} and calls == []
    rows[:] = [ConversionFileSummary("job_missing", "batch_a", "arquivo.pdf", "completed", now, now + timedelta(hours=1))]
    args["batches"].repository.get = lambda _: None
    unavailable = documents(**{**args, "category": "all"})["items"][0]
    assert unavailable["status"] == "unavailable" and unavailable["review_url"] is None
    assert unavailable["is_expired"] is False
