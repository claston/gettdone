from datetime import date

import pytest

from app.application import AccessControlService
from app.application.pro_organization import OrganizationConflict, OrganizationService


@pytest.fixture
def organization(tmp_path):
    access = AccessControlService(state_file=tmp_path / "access.json", token_secret="organization-test")
    owner = access.register_user(name="Owner", email="owner@example.test", password="strong-password")
    other = access.register_user(name="Other", email="other@example.test", password="strong-password")
    try:
        yield OrganizationService(access.db), owner.user_id, other.user_id, access
    finally:
        access.close()


def test_catalog_is_private_persistent_and_periods_do_not_require_clients(organization):
    service, owner, other, access = organization
    assert service.catalog(owner) == {"clients": [], "accounts": [], "periods": []}
    period = service.save_period(owner, name="Quinzena", start_date=date(2026, 9, 20), end_date=date(2026, 10, 4))
    client = service.save_client(owner, name="Empresa A", tax_id="12.345.678/0001-90", trade_name="Loja A")
    account = service.save_account(
        owner, client_id=client["id"], name="Conta principal", bank_code="033", branch="0001", account_number="00012345678901234567-8"
    )
    reloaded = OrganizationService(access.db).catalog(owner)
    assert reloaded["clients"][0]["tax_id"] == "12345678000190"
    assert reloaded["accounts"][0]["account_number"] == "00012345678901234567-8"
    assert reloaded["periods"][0]["start_date"] == "2026-09-20"
    assert reloaded["accounts"][0]["id"] == account["id"]
    assert service.catalog(other) == {"clients": [], "accounts": [], "periods": []}
    with pytest.raises(KeyError):
        service.save_account(other, client_id=client["id"], name="Foreign")
    with pytest.raises(KeyError):
        service.save_period(other, entity_id=period["id"], name="Foreign", start_date=date(2026, 9, 1), end_date=date(2026, 9, 2))


def test_conversion_context_is_independent_versioned_and_does_not_charge_quota(organization):
    service, owner, other, access = organization
    identity = access.resolve_identity(user_token=access.get_user_by_id(owner).token, anonymous_fingerprint=None)
    remaining = access.get_remaining_quota(identity)
    first = service.save_period(owner, name="Semana", start_date=date(2026, 10, 1), end_date=date(2026, 10, 7))
    second = service.save_period(owner, name="Quinzena", start_date=date(2026, 10, 1), end_date=date(2026, 10, 15))
    context = service.save_context(
        owner, job_id="job_1", client_id=None, account_id=None, period_ids=[first["id"], second["id"], first["id"]], expected_version=0
    )
    assert context["client_id"] is None
    assert set(context["period_ids"]) == {first["id"], second["id"]}
    assert context["version"] == 1
    assert service.contexts(other, ["job_1"]) == {}
    client = service.save_client(owner, name="Empresa A")
    changed = service.save_context(
        owner, job_id="job_1", client_id=client["id"], account_id=None, period_ids=[first["id"]], expected_version=1
    )
    assert changed["version"] == 2
    with pytest.raises(OrganizationConflict):
        service.save_context(owner, job_id="job_1", client_id=None, account_id=None, period_ids=[], expected_version=1)
    assert service.contexts(owner, ["job_1"])["job_1"]["client_id"] == client["id"]
    assert access.get_remaining_quota(identity) == remaining


def test_foreign_period_or_incompatible_account_changes_nothing(organization):
    service, owner, other, _access = organization
    first = service.save_client(owner, name="Primeiro")
    second = service.save_client(owner, name="Segundo")
    account = service.save_account(owner, client_id=second["id"], name="Conta do segundo")
    foreign = service.save_period(other, name="Outro", start_date=date(2026, 9, 1), end_date=date(2026, 9, 30))
    with pytest.raises(ValueError):
        service.save_context(owner, job_id="job_1", client_id=first["id"], account_id=account["id"], period_ids=[], expected_version=0)
    with pytest.raises(KeyError):
        service.save_context(owner, job_id="job_1", client_id=first["id"], account_id=None, period_ids=[foreign["id"]], expected_version=0)
    assert service.contexts(owner, ["job_1"]) == {}


def test_validation_duplicate_identity_and_editing_catalog(organization):
    service, owner, _other, _access = organization
    with pytest.raises(ValueError):
        service.save_period(owner, name="Invalid", start_date=date(2026, 10, 2), end_date=date(2026, 10, 1))
    with pytest.raises(ValueError):
        service.save_client(owner, name=" ")
    with pytest.raises(ValueError):
        service.save_client(owner, name="Invalid", tax_id="abc")
    client = service.save_client(owner, name="Original", tax_id="12345678901")
    with pytest.raises(ValueError):
        service.save_client(owner, name="Duplicate", tax_id="123.456.789-01")
    edited = service.save_client(owner, entity_id=client["id"], name="Corrigido", tax_id="12345678901")
    assert edited["id"] == client["id"]
    assert edited["name"] == "Corrigido"
