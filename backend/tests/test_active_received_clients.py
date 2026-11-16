from datetime import timedelta

import pytest

from tests import test_received_statements as helpers
from tests.test_received_statements import identity, record

receipts = helpers.receipts


@pytest.mark.parametrize("sort", ["id", "name"])
def test_active_filter_precedes_pagination_and_search_preserves_history(receipts, sort):
    service, owner, other = receipts
    now = record(owner).created_at
    for index in range(8):
        item = record(owner, f"client_{index}", days=0 if index % 2 == 0 else 1, source={
            **identity(), "holder_name": f"Cliente {index}", "holder_tax_id": None,
            "confidence": "source_account", "account_number": str(1000 + index),
        })
        service.receive(item)
    service.receive(record(other, "foreign"))
    seen, cursor = [], ""
    while True:
        page = service.dashboard(owner, now=now, active_only=True, sort=sort, after=cursor, limit=2, query="cliente")
        assert page["client_count"] == page["active_client_count"] == 4
        assert page["all_client_count"] == 8
        assert len(page["clients"]) == 2
        assert all(c["available_count"] == 1 for c in page["clients"])
        seen.extend(c["id"] for c in page["clients"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(set(seen)) == 4
    assert len(service.dashboard(owner, now=now)["clients"]) == 8
    assert service.dashboard(other, now=now, active_only=True, query="cliente")["clients"] == []


def test_mixed_client_counts_expiry_boundary_and_new_upload(receipts):
    service, owner, _ = receipts
    current = record(owner, "current")
    for status in ("completed", "running", "failed"):
        old = record(owner, f"old_{status}", days=0)
        old.status = status
        service.receive(old)
    current.status = "running"
    service.receive(current)
    page = service.dashboard(owner, now=current.created_at, active_only=True)
    client = page["clients"][0]
    assert (client["historical_count"], client["available_count"], client["expired_count"]) == (4, 1, 3)
    assert (client["attention_count"], client["processing_count"]) == (0, 1)
    assert page["next_expiration_at"] == current.expires_at.isoformat()
    assert page["server_time"] == current.created_at.isoformat()
    assert len(service.dashboard(owner, now=current.expires_at - timedelta(microseconds=1), active_only=True)["clients"]) == 1
    expired = service.dashboard(owner, now=current.expires_at, active_only=True)
    assert expired["clients"] == [] and expired["active_client_count"] == 0
    assert expired["all_client_count"] == 1 and expired["next_expiration_at"] is None
    historical = service.dashboard(owner, now=current.expires_at)["clients"][0]
    assert historical["attention_count"] == historical["processing_count"] == 0
    new = record(owner, "new", days=2)
    new.created_at = current.expires_at
    new.status = "failed"  # Still available for attention until its expiry.
    service.receive(new)
    restored = service.dashboard(owner, now=current.expires_at, active_only=True)["clients"][0]
    assert restored["id"] == client["id"] and restored["attention_count"] == 1


def test_expired_cursor_does_not_hide_remaining_active_clients(receipts):
    service, owner, _ = receipts
    now = record(owner).created_at
    for i, name in enumerate(("Alfa", "Beta", "Gama")):
        service.receive(record(owner, name, days=i + 1, source={
            **identity(), "holder_name": name, "holder_tax_id": None,
            "confidence": "source_account", "account_number": str(1000 + i),
        }))
    first = service.dashboard(owner, now=now, active_only=True, sort="name", limit=1)
    second = service.dashboard(owner, now=now + timedelta(days=1), active_only=True,
                               sort="name", limit=1, after=first["next_cursor"])
    assert [c["name"] for c in second["clients"]] == ["Beta"]


def test_empty_organization_has_active_counts_and_expiration_contract(receipts):
    service, owner, _ = receipts
    page = service.dashboard(owner, active_only=True)
    assert page["all_client_count"] == page["active_client_count"] == 0
    assert page["next_expiration_at"] is None and page["server_time"]
