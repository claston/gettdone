from pathlib import Path

import pytest

from app.application.access_control import AccessControlService
from app.application.access_control import access_control_helpers as helpers_module
from app.application.access_control import access_control_schema as schema_module
from app.dependencies import get_conversion_document_store, get_conversion_job_repository


def _stored_password_hash(service: AccessControlService, *, email: str) -> str:
    with service._connect() as conn:
        row = service._fetchone(
            conn,
            "SELECT password_hash FROM users WHERE email = ?",
            (email,),
        )
    assert row is not None
    return str(row["password_hash"])


def test_regular_tests_use_a_fast_password_hash_cost(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "fast-password-state.json",
        token_secret="test-secret",
    )

    service.register_user(name="Erica", email="fast@example.com", password="strong-pass")

    stored_hash = _stored_password_hash(service, email="fast@example.com")
    iterations = int(stored_hash.split("$", 3)[1])
    assert iterations < helpers_module.PRODUCTION_PASSWORD_HASH_ITERATIONS


@pytest.mark.production_password_hash
def test_production_password_hash_cost_remains_covered(tmp_path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "production-password-state.json",
        token_secret="test-secret",
    )

    registered = service.register_user(
        name="Erica",
        email="production@example.com",
        password="strong-pass",
    )

    stored_hash = _stored_password_hash(service, email="production@example.com")
    iterations = int(stored_hash.split("$", 3)[1])
    assert iterations == helpers_module.PRODUCTION_PASSWORD_HASH_ITERATIONS
    authenticated = service.authenticate_user(
        email="production@example.com",
        password="strong-pass",
    )
    assert authenticated.user_id == registered.user_id


def test_new_sqlite_services_clone_an_initialized_template(monkeypatch, tmp_path) -> None:
    def _unexpected_bootstrap(*_args, **_kwargs) -> None:
        raise AssertionError("fresh test databases must clone the initialized template")

    monkeypatch.setattr(
        schema_module,
        "apply_sqlite_legacy_schema_bootstrap",
        _unexpected_bootstrap,
    )

    service = AccessControlService(
        state_file=tmp_path / "template-clone-state.json",
        token_secret="test-secret",
    )

    identity = service.resolve_identity(
        anonymous_fingerprint="test-template-clone",
        user_token=None,
    )
    assert identity.identity_type == "anonymous"


def test_cloned_sqlite_databases_remain_isolated(tmp_path) -> None:
    first = AccessControlService(
        state_file=tmp_path / "first-state.json",
        token_secret="test-secret",
    )
    second = AccessControlService(
        state_file=tmp_path / "second-state.json",
        token_secret="test-secret",
    )

    first.register_user(name="Erica", email="isolated@example.com", password="strong-pass")

    assert first.authenticate_user(email="isolated@example.com", password="strong-pass")
    with second._connect() as conn:
        row = second._fetchone(
            conn,
            "SELECT COUNT(*) AS total FROM users WHERE email = ?",
            ("isolated@example.com",),
        )
    assert row is not None
    assert row["total"] == 0


def test_default_filesystem_runtime_is_not_shared_through_backend_tmp() -> None:
    job_repository = get_conversion_job_repository()
    document_store = get_conversion_document_store()

    backend_tmp = (Path(__file__).resolve().parents[1] / "tmp").resolve()
    assert not job_repository.root_dir.is_relative_to(backend_tmp)
    assert not document_store.root_dir.is_relative_to(backend_tmp)


def test_in_memory_http_databases_clone_schema_without_repeating_bootstrap(monkeypatch):
    from tests.test_convert_edits_http_real_api import _AccessControlServiceInMemory

    def unexpected_bootstrap(*args, **kwargs):
        raise AssertionError("HTTP fixtures must reuse the migrated SQLite template")

    monkeypatch.setattr(schema_module, "apply_sqlite_legacy_schema_bootstrap", unexpected_bootstrap)
    first = _AccessControlServiceInMemory(state_file=Path("memory-first.json"), token_secret="test")
    second = _AccessControlServiceInMemory(state_file=Path("memory-second.json"), token_secret="test")
    try:
        first.register_user(name="Owner", email="memory@example.test", password="strong-password")
        assert first._test_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert second._test_conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    finally:
        first._test_conn.close()
        second._test_conn.close()
