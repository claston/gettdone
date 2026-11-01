from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.application import AccessControlService
from app.application.received_statements import ReceivedStatements
from app.application.statement_identity import identify_statement


@pytest.fixture
def receipts(tmp_path):
    access = AccessControlService(state_file=tmp_path / "access.json", token_secret="receipts-test")
    owner = access.register_user(name="Owner", email="receipts@example.test", password="strong-password")
    other = access.register_user(name="Other", email="other-receipts@example.test", password="strong-password")
    try:
        yield ReceivedStatements(access.db), owner.user_id, other.user_id
    finally:
        access.close()


def record(owner, job="job_a", batch="batch_a", days=1, source=None):
    now = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
    return SimpleNamespace(
        job=SimpleNamespace(job_id=job, batch_id=batch, identity=SimpleNamespace(identity_type="user", identity_id=owner),
                            document=SimpleNamespace(filename=f"{job}.pdf")),
        created_at=now, expires_at=now + timedelta(days=days), status="completed",
        result=SimpleNamespace(payload={"organization_source": source or identity(), "analysis": {}}),
    )


def identity():
    return {"holder_name": "Padaria São José", "holder_tax_id": "12345678000190", "bank_code": "341",
            "branch": "0001", "account_number": "001234-5", "confidence": "strong", "references": [{"page": 1, "line": 1}]}


@pytest.mark.parametrize("query", ["padar", "SAO JOSE", "São José", "12345678000190", "12.345.678/0001-90", "0001-90"])
def test_client_search_normalizes_names_and_tax_ids_before_pagination(receipts, query):
    service, owner, other = receipts
    now = record(owner).created_at
    for index in range(4):
        service.receive(record(owner, f"search_{index}", source={
            **identity(), "holder_name": f"Padaria São José {index}", "holder_tax_id": None,
            "confidence": "source_account", "account_number": str(123400 + index),
        }))
    service.receive(record(owner))
    service.receive(record(other, "foreign"))
    service.receive(record(owner, "pending", source={"confidence": "ambiguous"}))
    expected = 5 if not any(char.isdigit() for char in query) else 1
    clients, cursor = [], ""
    while True:
        page = service.dashboard(owner, now=now, query=query, sort="name", after=cursor, limit=2)
        assert page["client_count"] == expected
        assert page["pending_count"] == 1
        assert [p["job_id"] for p in page["pending"]] == ["pending"]
        assert page["totals"]["statements"] == 6
        clients.extend(page["clients"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len({c["id"] for c in clients}) == expected
    empty = service.dashboard(owner, now=now, query="não existe", sort="name")
    assert empty["clients"] == [] and empty["client_count"] == 0 and empty["pending_count"] == 1
    assert len(service.dashboard(owner, now=now, query="  ")["clients"]) == 5


@pytest.mark.parametrize("query", ["joao", "JOÃO", "123.456.789-01", "12345678901"])
def test_search_cpf_and_foreign_only_matches_are_private(receipts, query):
    service, owner, other = receipts
    item = record(owner, source={**identity(), "holder_name": "João da Silva", "holder_tax_id": "12345678901"})
    service.receive(item)
    assert len(service.dashboard(owner, now=item.created_at, query=query)["clients"]) == 1
    assert service.dashboard(other, now=item.created_at, query=query)["clients"] == []


@pytest.mark.parametrize("confirmed", [False, True])
def test_later_inference_preserves_inline_edits_and_confirmed_identity(receipts, confirmed):
    service, owner, _ = receipts
    item = record(owner, source={"confidence": "ambiguous"} if confirmed else identity())
    service.receive(item)
    if confirmed:
        service.confirm(owner, item.job.job_id, expected_version=0, name="Nome confirmado",
                        tax_id="12345678000190", now=item.created_at)
    else:
        client = service.dashboard(owner, now=item.created_at)["clients"][0]
        service.edit_client(owner, client["id"], name="Nome confirmado", tax_id=client["tax_id"],
                            expected_updated_at=client["updated_at"])
    later = record(owner, "later", source={**identity(), "holder_name": "Inferência posterior"})
    later.created_at += timedelta(minutes=1)
    service.receive(later)
    service.receive(item)
    client = service.dashboard(owner, now=later.created_at)["clients"][0]
    assert client["name"] == "Nome confirmado" and client["historical_count"] == 2


@pytest.mark.parametrize("legacy_schema", [True, False])
def test_sqlite_upgrade_protects_old_identities_without_freezing_new_inferences_on_restart(tmp_path, legacy_schema):
    state = tmp_path / "upgrade.json"
    with_access = AccessControlService(state_file=state, token_secret="client-upgrade-test")
    try:
        owner = with_access.register_user(name="Owner", email="upgrade@example.test", password="strong-password").user_id
        service = ReceivedStatements(with_access.db)
        service.receive(record(owner))
        if legacy_schema:
            with with_access.db.connect() as conn:
                service._write(conn, "DROP TABLE pro_client_identity_confirmations")
                conn.commit()
    finally:
        with_access.close()
    reopened = AccessControlService(state_file=state, token_secret="client-upgrade-test")
    try:
        service = ReceivedStatements(reopened.db)
        later = record(owner, "later", source={**identity(), "holder_name": "Nome posterior"})
        later.created_at += timedelta(minutes=1)
        service.receive(later)
        assert service.dashboard(owner, now=later.created_at)["clients"][0]["name"] == (
            "Padaria São José" if legacy_schema else "Nome posterior")
    finally:
        reopened.close()


@pytest.mark.parametrize("days", [1, 30])
def test_multiple_batches_same_client_retention_boundary_and_history_survive_deletion(receipts, days):
    service, owner, other = receipts
    first = record(owner, days=days)
    second = record(owner, "job_b", "batch_b", days=days + 1)
    service.receive(first)
    service.receive(second)
    service.receive(first)  # queue redelivery
    before = service.dashboard(owner, now=first.expires_at - timedelta(microseconds=1))
    assert before["totals"] == {"statements": 2, "ready": 2, "attention": 0}
    assert len(before["clients"]) == 1
    client = before["clients"][0]
    assert (client["historical_count"], client["available_count"], client["expired_count"]) == (2, 2, 0)
    assert client["accounts"][0]["statement_count"] == 2
    at_page = service.dashboard(owner, now=first.expires_at)
    assert at_page["totals"] == {"statements": 1, "ready": 1, "attention": 0}
    at = at_page["clients"][0]
    assert (at["historical_count"], at["available_count"], at["expired_count"]) == (2, 1, 1)
    assert [row["job_id"] for row in service.documents(owner, client["id"], now=first.expires_at)] == ["job_b"]
    first.expires_at += timedelta(days=90)
    service.receive(first)  # never renew old retention
    expired_page = service.dashboard(owner, now=second.expires_at)
    assert expired_page["totals"] == {"statements": 0, "ready": 0, "attention": 0}
    assert expired_page["clients"][0]["available_count"] == 0
    assert service.dashboard(other)["clients"] == []
    with pytest.raises(KeyError):
        service.documents(other, client["id"])


def test_first_visit_with_four_expired_statements_has_zero_operational_totals(receipts):
    service, owner, other = receipts
    for index in range(4):
        service.receive(record(owner, job=f"old_{index}"))
    service.receive(record(other, job="foreign_active", days=30))
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    page = service.dashboard(owner, now=now)
    assert page["totals"] == {"statements": 0, "ready": 0, "attention": 0}
    client = page["clients"][0]
    assert (client["historical_count"], client["available_count"], client["expired_count"]) == (4, 0, 4)
    assert client["accounts"][0]["statement_count"] == 4
    assert page["pending"] == [] and service.documents(owner, client["id"], now=now) == []


def test_totals_exclude_expired_ready_failed_and_processing_documents(receipts):
    service, owner, _ = receipts
    for expired in (False, True):
        for status in ("completed", "failed", "running"):
            item = record(owner, job=f"{status}_{expired}", days=1 if expired else 30)
            item.status = status
            service.receive(item)
    page = service.dashboard(owner, now=datetime(2026, 10, 30, 12, tzinfo=timezone.utc))
    assert page["totals"] == {"statements": 3, "ready": 1, "attention": 1}
    assert page["clients"][0]["historical_count"] == 6


def test_uncertain_identity_is_pending_and_does_not_merge_by_name(receipts):
    service, owner, _ = receipts
    service.receive(record(owner))
    uncertain = {**identity(), "holder_tax_id": "", "account_number": "", "confidence": "name"}
    service.receive(record(owner, "job_b", source=uncertain))
    dashboard = service.dashboard(owner, now=datetime(2026, 10, 28, 13, tzinfo=timezone.utc))
    assert len(dashboard["clients"]) == 1
    assert [item["job_id"] for item in dashboard["pending"]] == ["job_b"]
    assert dashboard["pending"][0]["source"]["holder_name"] == "Padaria São José"


@pytest.mark.parametrize("tax", ["12345678000190", "12345678901"])
def test_matching_tax_prefers_current_document_name_without_creating_another_client(receipts, tax):
    service, owner, other = receipts
    old = record(owner, source={**identity(), "holder_name": "ASAAS", "holder_tax_id": tax}, days=30)
    service.receive(old)
    before = service.dashboard(owner, now=old.created_at)["clients"][0]
    current = record(owner, "job_new", source={**identity(), "holder_tax_id": tax}, days=30)
    current.created_at += timedelta(minutes=1)
    service.receive(current)
    service.receive(old)
    page = service.dashboard(owner, now=current.created_at)
    assert len(page["clients"]) == 1
    assert page["clients"][0]["id"] == before["id"]
    assert page["clients"][0]["name"] == "Padaria São José"
    assert page["clients"][0]["historical_count"] == 2
    assert service.dashboard(other)["clients"] == []


def test_already_linked_receipt_can_refresh_legacy_client_name_from_its_identified_holder(receipts):
    service, owner, _ = receipts
    item = record(owner, days=30)
    service.receive(item)
    client = service.dashboard(owner, now=item.created_at)["clients"][0]
    with service.db.connect() as conn:
        service._write(conn, "UPDATE pro_clients SET name = ? WHERE id = ?", ("ASAAS", client["id"]))
        conn.commit()
    service.receive(item)
    assert service.dashboard(owner, now=item.created_at)["clients"][0]["name"] == "Padaria São José"


def test_ambiguous_document_cannot_rename_a_client_even_with_the_same_tax(receipts):
    service, owner, _ = receipts
    service.receive(record(owner, days=30))
    current = record(owner, "uncertain", source={**identity(), "holder_name": "Nome incerto", "confidence": "ambiguous"}, days=30)
    current.created_at += timedelta(minutes=1)
    service.receive(current)
    page = service.dashboard(owner, now=current.created_at)
    assert page["clients"][0]["name"] == "Padaria São José"
    assert [row["job_id"] for row in page["pending"]] == ["uncertain"]


def test_different_tax_does_not_rename_or_merge_an_existing_client(receipts):
    service, owner, _ = receipts
    first = record(owner, days=30)
    service.receive(first)
    existing_id = service.dashboard(owner, now=first.created_at)["clients"][0]["id"]
    other = record(owner, "job_other_tax", source={**identity(), "holder_name": "Outro titular", "holder_tax_id": "98765432000110"})
    other.created_at += timedelta(minutes=1)
    service.receive(other)
    clients = service.dashboard(owner, now=other.created_at)["clients"]
    assert len(clients) == 2
    assert next(client for client in clients if client["id"] == existing_id)["name"] == "Padaria São José"



def test_known_source_account_and_multiple_accounts_keep_document_totals_unique(receipts):
    service, owner, _ = receipts
    source = {**identity(), "accounts": [
        {"bank_code": "341", "branch": "0001", "account_number": "001234-5"},
        {"bank_code": "077", "branch": "0001", "account_number": "009876-5"}]}
    service.receive(record(owner, source=source))
    service.receive(record(owner, "job_b", source={**identity(), "holder_tax_id": "", "confidence": "name"}))
    dashboard = service.dashboard(owner, now=datetime(2026, 10, 28, 13, tzinfo=timezone.utc))
    assert dashboard["totals"]["statements"] == 2
    assert len(dashboard["clients"]) == 1 and dashboard["pending"] == []
    client = dashboard["clients"][0]
    assert client["historical_count"] == 2 and len(client["accounts"]) == 2
    assert sorted(account["statement_count"] for account in client["accounts"]) == [1, 2]


def test_pending_pagination_and_completion_after_early_refresh(receipts):
    service, owner, _ = receipts
    first = record(owner)
    first.status, first.result = "running", None
    service.receive(first)
    service.receive(record(owner))
    now = datetime(2026, 10, 28, 13, tzinfo=timezone.utc)
    assert service.dashboard(owner, now=now)["clients"][0]["historical_count"] == 1
    uncertain = {"holder_name": "Titular sem documento", "confidence": "name"}
    for index in range(52):
        service.receive(record(owner, f"pending_{index:03}", source=uncertain))
    first_page = service.dashboard(owner, now=now)
    assert len(first_page["pending"]) == 50
    second_page = service.dashboard(owner, now=now, pending_after=first_page["pending_next_cursor"])
    assert len(second_page["pending"]) == 2 and second_page["pending_next_cursor"] is None


def test_confirmation_is_versioned_and_replay_preserves_user_corrections(receipts):
    service, owner, _ = receipts
    item = record(owner, source={**identity(), "holder_tax_id": "", "confidence": "name"})
    service.receive(item)
    now = datetime(2026, 10, 28, 13, tzinfo=timezone.utc)
    service.confirm(owner, "job_a", expected_version=0, name="Padaria corrigida", tax_id="12345678000190", now=now)
    service.receive(item)
    client = service.dashboard(owner, now=now)["clients"][0]
    assert client["name"] == "Padaria corrigida"
    with pytest.raises(ValueError):
        service.confirm(owner, "job_a", expected_version=0, name="Outro", tax_id="12345678000190", now=now)
    with pytest.raises(ValueError):
        service.confirm(owner, "job_a", expected_version=1, name="Outro", tax_id="12345678000190", now=item.expires_at)


def test_identification_requires_holder_labels_preserves_provenance_and_supports_ocr():
    pages = ["Banco Exemplo CNPJ: 99.999.999/0001-99\nTitular: Padaria São José\n"
             "CPF/CNPJ do titular: 12.345.678/0001-90\nAgência: 0001 Conta: 001234-5\n01/10 PIX João CPF 111.111.111-11 10,00"]
    source = identify_statement({"bank_code": "341"}, page_texts=pages, representation="ocr")
    assert source["holder_name"] == "Padaria São José"
    assert source["holder_tax_id"] == "12345678000190"
    assert source["confidence"] == "strong"
    assert source["references"] and source["representation"] == "ocr"
    assert source["account_number"] == "001234-5"
    assert not identify_statement({}, page_texts=["Banco CNPJ 99.999.999/0001-99\nPIX CPF 111.111.111-11"])["holder_tax_id"]
    conflicting = identify_statement({}, page_texts=pages + ["Titular: Outra empresa\nCPF/CNPJ do titular: 11.111.111/0001-11"])
    assert conflicting["confidence"] == "ambiguous"


def test_alphabetical_clients_across_pages_with_accents_ties_and_legacy_cursors(receipts):
    service, owner, other = receipts
    now = datetime(2026, 10, 28, 13, tzinfo=timezone.utc)
    names = ["Zulu", "Érica", "beatriz", "Ângela", "Álvaro", "alvaro", "Álvaro"]
    for index, name in enumerate(names):
        service.receive(record(owner, f"name_{index}", source={
            **identity(), "holder_name": name, "holder_tax_id": f"{index:014d}",
        }))
    service.receive(record(other, "foreign", source={**identity(), "holder_name": "AAA outro usuário"}))
    legacy = service.dashboard(owner, now=now)
    assert [c["id"] for c in legacy["clients"]] == sorted(c["id"] for c in legacy["clients"])
    clients, cursor = [], ""
    while True:
        page = service.dashboard(owner, now=now, after=cursor, limit=2, sort="name")
        clients.extend(page["clients"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert [c["name"] for c in clients[:3]].count("Álvaro") == 2
    assert {c["name"] for c in clients[:3]} == {"Álvaro", "alvaro"}
    assert [c["id"] for c in clients[:3]] == sorted(c["id"] for c in clients[:3])
    assert [c["name"] for c in clients[3:]] == ["Ângela", "beatriz", "Érica", "Zulu"]
    assert len({c["id"] for c in clients}) == len(names)
    foreign_id = service.dashboard(other, now=now)["clients"][0]["id"]
    assert service.dashboard(owner, now=now, after=foreign_id, sort="name")["clients"] == []
