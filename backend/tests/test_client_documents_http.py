import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application.conversion.contracts.batches import ConversionFileSummary
from app.dependencies import get_access_control_service
from app.main import app
from app.routers.pro_documents import document_category, documents
from tests.http_runtime import LoopbackClient
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server

pytestmark = pytest.mark.real_http


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


def test_document_inventory_bounded_sparse_pagination_and_expiration():
    now = datetime.now(timezone.utc)
    rows = [ConversionFileSummary(f"job_{index:03d}", "batch_a", "arquivo.pdf", "completed",
            now - timedelta(seconds=index), now + timedelta(hours=1)) for index in range(101)]
    calls = []

    def listed(**kwargs):
        assert kwargs["identity_id"] == "owner"
        return tuple(row for row in rows if kwargs["before"] is None or (row.created_at, row.job_id) < kwargs["before"])[:kwargs["limit"]]

    def get(job_id):
        calls.append(job_id)
        return SimpleNamespace(job=SimpleNamespace(identity=SimpleNamespace(identity_type="user", identity_id="owner")),
            expires_at=now + timedelta(hours=1), status="completed",
            result=SimpleNamespace(payload={"analysis": {"document_type": "bank_receipt" if job_id == "job_100" else "unknown"},
                                           "private_financial_rows": "must not be returned"}))

    args = dict(owner="owner", service=SimpleNamespace(contexts=lambda *_: {}),
        batches=SimpleNamespace(repository=SimpleNamespace(list_files_for_owner=listed, get=get)),
        client_id=None, batch_id=None, unassigned=False, category="receipts", cursor=None, limit=30)
    first = documents(**args)
    assert first["items"] == [] and first["next_cursor"] and len(calls) == 100
    second = documents(**{**args, "cursor": first["next_cursor"]})
    assert [item["job_id"] for item in second["items"]] == ["job_100"]
    assert second["next_cursor"] is None and "private_financial_rows" not in str(second)
    rows[:] = [ConversionFileSummary("job_old", "batch_a", "arquivo.pdf", "completed", now - timedelta(days=2), now - timedelta(days=1))]
    calls.clear()
    expired = documents(**{**args, "category": "all"})["items"][0]
    assert expired["is_expired"] and expired["review_url"] is None and calls == []
    rows[:] = [ConversionFileSummary("job_missing", "batch_a", "arquivo.pdf", "completed", now, now + timedelta(hours=1))]
    args["batches"].repository.get = lambda _: None
    unavailable = documents(**{**args, "category": "all"})["items"][0]
    assert unavailable["status"] == "unavailable" and unavailable["review_url"] is None
    assert unavailable["is_expired"] is False
