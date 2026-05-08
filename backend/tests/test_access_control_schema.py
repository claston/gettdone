from __future__ import annotations

from contextlib import contextmanager
from threading import RLock

from app.application import access_control_schema as access_control_schema_module
from app.application.access_control_schema import AccessControlSchemaComponent


class _DummyConn:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


class _FakeService:
    def __init__(self, *, use_postgres: bool) -> None:
        self._use_postgres = use_postgres
        self.database_schema = "public"
        self._lock = RLock()
        self._conn = _DummyConn()

    @contextmanager
    def _connect(self):
        yield self._conn


def test_postgres_schema_ready_allows_startup(monkeypatch) -> None:
    service = _FakeService(use_postgres=True)
    schema = AccessControlSchemaComponent(service)

    monkeypatch.setattr(schema, "_postgres_table_exists", lambda conn, table_name: True)
    monkeypatch.setattr(schema, "_postgres_column_exists", lambda conn, table_name, column_name: True)

    schema.init_db()


def test_postgres_schema_missing_objects_raises_actionable_error(monkeypatch) -> None:
    service = _FakeService(use_postgres=True)
    schema = AccessControlSchemaComponent(service)

    monkeypatch.setattr(
        schema,
        "_postgres_table_exists",
        lambda conn, table_name: table_name != "user_sessions",
    )
    monkeypatch.setattr(
        schema,
        "_postgres_column_exists",
        lambda conn, table_name, column_name: not (table_name == "checkout_intents" and column_name == "released_at"),
    )

    try:
        schema.init_db()
        assert False, "Expected RuntimeError for incomplete PostgreSQL schema."
    except RuntimeError as exc:
        message = str(exc)
        assert "alembic upgrade head" in message
        assert "alembic stamp 20260508_01" in message
        assert "missing tables: user_sessions" in message
        assert "missing columns: checkout_intents.released_at" in message


def test_sqlite_init_delegates_to_legacy_bootstrap(monkeypatch) -> None:
    service = _FakeService(use_postgres=False)
    schema = AccessControlSchemaComponent(service)
    called = {"count": 0}

    def _fake_bootstrap(received_service, received_conn) -> None:
        called["count"] += 1
        assert received_service is service
        assert received_conn is service._conn

    monkeypatch.setattr(access_control_schema_module, "apply_sqlite_legacy_schema_bootstrap", _fake_bootstrap)

    schema.init_db()

    assert called["count"] == 1
    assert service._conn.commits == 1
