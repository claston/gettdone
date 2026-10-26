import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.application.conversion.contracts.jobs import ConversionJobResultReference
from app.application.organization_suggestions import build_source_evidence, suggest_organization
from app.dependencies import get_access_control_service, get_conversion_batch_service
from app.main import app
from app.routers.pro_organization import conversion_page
from tests.test_conversion_workspace_http_real_api import _run_pro_upload_http_server


def test_suggestions_distinguish_holder_source_account_and_work_dates():
    catalog = {
        "clients": [{"id": "c1", "name": "Loja", "trade_name": "", "tax_id": "12345678000190"}],
        "accounts": [{"id": "a1", "client_id": "c1", "bank_code": "033", "branch": "0001", "account_number": "000123-4"}],
        "periods": [],
    }
    analysis = {
        "bank_code": "033", "bank_branch": "0001", "account_number": "000123-4",
        "financial_document": {"metadata": {"owner_tax_id": "12.345.678/0001-90", "owner_name": "Loja"}},
        "preview_transactions": [{"date": "2026-09-20"}, {"date": "2026-10-19"}],
    }
    source = build_source_evidence(analysis)
    result = suggest_organization(source, catalog)
    assert result["clients"][0]["id"] == "c1"
    assert result["clients"][0]["basis"] == "holder_tax_id"
    assert result["accounts"][0]["id"] == "a1"
    assert result["period"]["basis"] == "movement_dates"
    assert result["period"]["document_coverage"] is False
    # A destination override and counterparty/bank identifiers cannot become source evidence.
    analysis.update(account_number="999999", tax_id="12345678000190")
    assert source["account_number"] == "000123-4"
    assert suggest_organization(build_source_evidence({"tax_id": "12345678000190"}), catalog)["clients"] == []
    assert suggest_organization(build_source_evidence({}), catalog)["period"] is None


def test_ambiguous_names_and_accounts_remain_unconfirmed_and_conflicting_tax_ids_do_not_match():
    source = build_source_evidence({"financial_document": {"metadata": {"owner_name": "José Silva"}}})
    clients = [{"id": str(n), "name": "Jose Silva", "trade_name": "", "tax_id": str(n)} for n in (1, 2)]
    result = suggest_organization(source, {"clients": clients, "accounts": [], "periods": []})
    assert len(result["clients"]) == 2
    assert all(item["confidence"] == "ambiguous" for item in result["clients"])
    source["holder_tax_id"] = "12345678901"
    assert suggest_organization(source, {"clients": clients, "accounts": [], "periods": []})["clients"] == []


def test_sparse_filters_find_matches_across_metadata_chunks_and_preserve_pagination():
    now = datetime.now(timezone.utc)
    records = [SimpleNamespace(job_id=f"job_{n:03}", batch_id="batch_1", filename=f"{n}.pdf", status="completed",
                               created_at=now - timedelta(seconds=n), expires_at=now + timedelta(days=1)) for n in range(205)]

    class Repository:
        def list_files_for_owner(self, *, identity_type, identity_id, limit, before):
            assert (identity_type, identity_id) == ("user", "owner")
            return tuple(row for row in records if before is None or (row.created_at, row.job_id) < before)[:limit]

    class Organization:
        def contexts(self, owner, ids):
            assert owner == "owner"
            return {job: {"client_id": "chosen", "account_id": None, "period_ids": [], "version": 1}
                    for job in ids if job in {"job_102", "job_204"}}

    cursor = None
    found = []
    while True:
        page = conversion_page("owner", SimpleNamespace(repository=Repository()), Organization(), cursor, 1, "chosen", None, None, False)
        found.extend(row["job_id"] for row in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert found == ["job_102", "job_204"]


@pytest.mark.real_http
def test_http_independent_listing_filtered_pages_bulk_conflicts_and_suggestions(tmp_path, monkeypatch):
    monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "true")
    with _run_pro_upload_http_server(tmp_path) as (base, publisher, token):
        access = app.dependency_overrides[get_access_control_service]()
        batches = app.dependency_overrides[get_conversion_batch_service]()
        identity = access.resolve_identity(anonymous_fingerprint=None, user_token=token)
        remaining = access.get_remaining_quota(identity)
        headers = {"Authorization": f"Bearer {token}", "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"}
        with httpx.Client(base_url=base, headers=headers) as client:
            raw = b"date,amount,description\n2026-10-01,12,PIX\n"
            created = client.post("/api/pro/conversion-batches", headers={"Idempotency-Key": "org-v2"}, json={"files": [
                {"filename": f"{n}.csv", "content_type": "text/csv", "size_bytes": len(raw),
                 "sha256_hex": hashlib.sha256(raw).hexdigest()} for n in range(3)
            ]})
            assert created.status_code == 201, created.text
            batch_id = created.json()["batch_id"]
            rows = client.get("/api/pro/organization/conversions").json()["items"]
            person = client.post("/api/pro/organization/clients", json={"name": "Loja"}).json()
            period = client.post("/api/pro/organization/periods", json={
                "name": "Quinzena", "start_date": "2026-09-20", "end_date": "2026-10-04",
            }).json()
            oldest = rows[-1]
            path = f"/api/pro/organization/batches/{batch_id}/jobs/{oldest['job_id']}/context"
            assert client.put(path, json={"client_id": person["id"], "period_ids": [period["id"]]}).status_code == 200
            filtered = client.get("/api/pro/organization/conversions", params={"limit": 1, "client_id": person["id"]})
            assert filtered.status_code == 200, filtered.text
            assert [row["job_id"] for row in filtered.json()["items"]] == [oldest["job_id"]]
            assert filtered.json()["next_cursor"] is None
            changes = [{"batch_id": batch_id, "job_id": row["job_id"], "context": {
                "client_id": None, "account_id": None, "period_ids": [period["id"]], "expected_version": 0,
            }} for row in rows]
            changed = client.post("/api/pro/organization/contexts/bulk", json={"items": changes})
            assert changed.status_code == 200, changed.text
            assert [row["status"] for row in changed.json()["items"]] == [200, 200, 409]
            assert client.post("/api/pro/organization/contexts/bulk", json={"items": [changes[0], changes[0]]}).status_code == 400
            # A foreign file must be rejected before any item is modified.
            foreign = access.register_user(name="Other", email="other-v2@example.test", password="strong-password")
            access.activate_user_plan(user_id=foreign.user_id, plan_code="essencial")
            rejected = client.post("/api/pro/organization/contexts/bulk", json={"items": changes},
                                   headers={"Authorization": f"Bearer {foreign.token}"})
            assert rejected.status_code == 404
            job_id = oldest["job_id"]
            batches.repository.mark_uploaded(job_id)
            batches.repository.mark_queued(job_id)
            batches.repository.mark_running(job_id)
            source = build_source_evidence({"preview_transactions": [{"date": "2026-10-01"}]})
            batches.repository.mark_completed(job_id, result=ConversionJobResultReference(
                analysis_id="an_org_v2", payload={"organization_source": source, "analysis": {"account_number": "destination"}},
            ))
            suggestions = client.get(f"/api/pro/organization/batches/{batch_id}/jobs/{job_id}/suggestions")
            assert suggestions.status_code == 200, suggestions.text
            assert suggestions.json()["period"]["basis"] == "movement_dates"
            assert client.get(f"/api/pro/organization/batches/{batch_id}/jobs/{job_id}/suggestions",
                              headers={"Authorization": f"Bearer {foreign.token}"}).status_code == 404
            monkeypatch.setenv("PRO_ORGANIZATION_ENABLED", "false")
            assert client.get("/api/pro/organization/catalog").status_code == 503
            # No dependency on organization storage when requesting the independent module.
            from app.routers.pro_organization import organization_service
            app.dependency_overrides[organization_service] = lambda: pytest.fail("Organization dependency called")
            try:
                plain = client.get("/api/pro/conversions", params={"limit": 1})
                assert plain.status_code == 200, plain.text
                assert len(plain.json()["items"]) == 1 and plain.json()["next_cursor"]
                assert client.get("/api/pro/conversions", params={"client_id": person["id"]}).status_code == 403
                assert httpx.get(base + "/api/pro/conversions").status_code == 401
            finally:
                app.dependency_overrides.pop(organization_service, None)
            assert access.get_remaining_quota(identity) == remaining
            assert publisher.calls == []
