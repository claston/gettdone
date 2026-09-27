from contextlib import contextmanager
from datetime import datetime, timezone
from threading import RLock

import pytest

from app.application.conversion.conversion_access import PostgresConversionAccessService
from app.application.conversion.identity import IdentityContext
from app.application.errors import FileTooLargeError, QuotaExceededError


class FakeCursor:
    def __init__(self, connection) -> None:
        self.connection = connection
        self.rowcount = 0
        self._row = None

    def execute(self, query: str, params: tuple = ()) -> None:
        normalized = " ".join(query.split())
        if normalized.startswith("SET search_path"):
            return
        if normalized.startswith("INSERT INTO usage") and "DO NOTHING" in normalized:
            key = (params[0], params[1])
            self.rowcount = int(key not in self.connection.usage)
            self.connection.usage.setdefault(
                key,
                {
                    "used_count": params[2],
                    "quota_limit": params[3],
                    "updated_at": params[4],
                    "window_started_at": params[5],
                },
            )
            return
        if normalized.startswith("SELECT identity_type"):
            key = (params[0], params[1])
            self._row = {"identity_type": key[0]} if key in self.connection.usage else None
            return
        if normalized.startswith("SELECT used_count"):
            self._row = self.connection.usage.get((params[0], params[1]))
            return
        if normalized.startswith("INSERT INTO conversion_quota_consumptions"):
            key = (params[0], params[1], params[2])
            self.rowcount = int(key not in self.connection.consumptions)
            self.connection.consumptions.add(key)
            return
        if normalized.startswith("INSERT INTO usage") and "DO UPDATE SET" in normalized:
            key = (params[0], params[1])
            self.connection.usage[key] = {
                "used_count": params[2],
                "quota_limit": params[3],
                "updated_at": params[4],
                "window_started_at": params[5],
            }
            return
        raise AssertionError(f"Unexpected query: {normalized}")

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def fetchone(self):
        return self._row

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self) -> None:
        self.usage: dict[tuple[str, str], dict[str, object]] = {}
        self.consumptions: set[tuple[str, str, str]] = set()
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1


class FakePool:
    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    @contextmanager
    def connection(self, *, timeout: float):
        assert timeout == 5.0
        yield self._connection


def _service(connection: FakeConnection) -> PostgresConversionAccessService:
    service = PostgresConversionAccessService.__new__(PostgresConversionAccessService)
    service.database_schema = "public"
    service.db_pool_timeout_seconds = 5.0
    service.now_provider = lambda: datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    service._lock = RLock()
    service._pool = FakePool(connection)
    return service


def test_worker_access_consumes_quota_idempotently() -> None:
    connection = FakeConnection()
    service = _service(connection)
    identity = IdentityContext(identity_type="user", identity_id="usr_123", quota_limit=3)

    assert service.consume_quota(identity, consumed_units=2, idempotency_key="job_123") == 1
    assert service.consume_quota(identity, consumed_units=2, idempotency_key="job_123") == 1
    assert connection.usage[("user", "usr_123")]["used_count"] == 2
    assert connection.consumptions == {("user", "usr_123", "job_123")}

    with pytest.raises(QuotaExceededError):
        service.ensure_quota_available(identity, required_units=2)


def test_worker_access_enforces_upload_limit_without_authentication_service() -> None:
    service = _service(FakeConnection())

    service.assert_upload_size(b"1234", max_upload_size_bytes=4)
    with pytest.raises(FileTooLargeError):
        service.assert_upload_size(b"12345", max_upload_size_bytes=4)


def test_worker_access_rejects_invalid_database_schema_before_opening_pool() -> None:
    with pytest.raises(ValueError, match="valid PostgreSQL identifier"):
        PostgresConversionAccessService(
            database_url="postgresql://worker:test@database.example/gettdone",
            database_schema="public; DROP SCHEMA public",
        )


def test_worker_access_allows_pool_to_start_with_zero_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg_pool

    captured: dict[str, object] = {}

    class CapturingPool:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", CapturingPool)

    PostgresConversionAccessService(
        database_url="postgresql://worker:test@database.example/gettdone",
        db_pool_min_size=0,
        db_pool_max_size=1,
    )

    assert captured["min_size"] == 0
    assert captured["max_size"] == 1
