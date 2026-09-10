from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Iterator

from app.application.anonymous_conversion_history import (
    record_anonymous_conversion_event as record_anonymous_conversion_event_query,
)
from app.application.conversion.identity import IdentityContext
from app.application.conversion_history import record_user_conversion as record_user_conversion_query
from app.application.errors import FileTooLargeError
from app.application.quota_management import (
    compute_remaining_quota,
    persist_consumed_usage,
    read_usage_snapshot,
    require_quota_available,
)

_DATABASE_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PostgresConversionAccessService:
    """Worker-only access adapter without authentication or admin capabilities."""

    def __init__(
        self,
        *,
        database_url: str,
        database_schema: str = "public",
        db_pool_min_size: int = 1,
        db_pool_max_size: int = 1,
        db_pool_timeout_seconds: float = 5.0,
        now_provider=None,
    ) -> None:
        normalized_url = (database_url or "").strip()
        if not normalized_url.startswith(("postgres://", "postgresql://")):
            raise ValueError("Worker conversion access requires a PostgreSQL database URL.")
        normalized_schema = (database_schema or "public").strip() or "public"
        if _DATABASE_SCHEMA_RE.fullmatch(normalized_schema) is None:
            raise ValueError("DATABASE_SCHEMA must be a valid PostgreSQL identifier.")

        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except Exception as exc:  # pragma: no cover - installed in the worker image
            raise RuntimeError("Worker conversion access requires psycopg with pool support.") from exc

        self.database_schema = normalized_schema
        self.db_pool_timeout_seconds = max(1.0, float(db_pool_timeout_seconds))
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._lock = RLock()
        min_size = max(1, int(db_pool_min_size))
        max_size = max(min_size, int(db_pool_max_size))
        self._pool = ConnectionPool(
            conninfo=normalized_url,
            kwargs={"row_factory": dict_row},
            min_size=min_size,
            max_size=max_size,
            timeout=self.db_pool_timeout_seconds,
            open=True,
        )

    def close(self) -> None:
        pool = self._pool
        if pool is not None:
            pool.close()
            self._pool = None

    def assert_upload_size(self, raw_bytes: bytes, max_upload_size_bytes: int) -> None:
        if len(raw_bytes) > max(1, int(max_upload_size_bytes)):
            raise FileTooLargeError

    def ensure_quota_available(self, identity: IdentityContext, *, required_units: int = 1) -> None:
        with self._lock:
            with self._connect() as conn:
                snapshot = self._read_usage_snapshot(conn, identity=identity)
                require_quota_available(
                    used_count=snapshot.used_count,
                    quota_limit=identity.quota_limit,
                    required_units=required_units,
                )
                conn.commit()

    def consume_quota(
        self,
        identity: IdentityContext,
        *,
        consumed_units: int = 1,
        idempotency_key: str | None = None,
    ) -> int:
        normalized_key = (idempotency_key or "").strip()
        if len(normalized_key) > 200:
            raise ValueError("Quota consumption idempotency key is too long.")

        with self._lock:
            with self._connect() as conn:
                self._ensure_and_lock_usage(conn, identity=identity)
                if normalized_key and not self._reserve_consumption(
                    conn,
                    identity=identity,
                    idempotency_key=normalized_key,
                    consumed_units=consumed_units,
                ):
                    snapshot = self._read_usage_snapshot(conn, identity=identity)
                    conn.commit()
                    return compute_remaining_quota(
                        used_count=snapshot.used_count,
                        quota_limit=identity.quota_limit,
                    )

                snapshot = self._read_usage_snapshot(conn, identity=identity)
                next_snapshot = persist_consumed_usage(
                    conn,
                    identity=identity,
                    snapshot=snapshot,
                    consumed_units=consumed_units,
                    now_provider=self.now_provider,
                    execute=self._execute,
                )
                conn.commit()
                return compute_remaining_quota(
                    used_count=next_snapshot.used_count,
                    quota_limit=identity.quota_limit,
                )

    def record_user_conversion(self, **kwargs: Any) -> None:
        with self._lock:
            with self._connect() as conn:
                record_user_conversion_query(
                    conn,
                    execute=self._execute,
                    now_iso=self.now_provider().isoformat(),
                    **kwargs,
                )
                conn.commit()

    def record_anonymous_conversion_event(self, **kwargs: Any) -> None:
        with self._lock:
            with self._connect() as conn:
                record_anonymous_conversion_event_query(
                    conn,
                    execute=self._execute,
                    created_at=self.now_provider().isoformat(),
                    **kwargs,
                )
                conn.commit()

    def _read_usage_snapshot(self, conn, *, identity: IdentityContext):
        return read_usage_snapshot(
            conn,
            identity=identity,
            now_provider=self.now_provider,
            fetchone=self._fetchone,
            execute=self._execute,
            parse_usage_datetime=self._parse_usage_datetime,
            is_quota_window_expired=self._is_quota_window_expired,
        )

    def _ensure_and_lock_usage(self, conn, *, identity: IdentityContext) -> None:
        now_iso = self.now_provider().isoformat()
        cursor = self._execute(
            conn,
            """
            INSERT INTO usage (
                identity_type, identity_id, used_count, quota_limit, updated_at, window_started_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(identity_type, identity_id) DO NOTHING
            """,
            (identity.identity_type, identity.identity_id, 0, identity.quota_limit, now_iso, now_iso),
        )
        cursor.close()
        cursor = self._execute(
            conn,
            """
            SELECT identity_type
            FROM usage
            WHERE identity_type = ? AND identity_id = ?
            FOR UPDATE
            """,
            (identity.identity_type, identity.identity_id),
        )
        try:
            cursor.fetchone()
        finally:
            cursor.close()

    def _reserve_consumption(
        self,
        conn,
        *,
        identity: IdentityContext,
        idempotency_key: str,
        consumed_units: int,
    ) -> bool:
        cursor = self._execute(
            conn,
            """
            INSERT INTO conversion_quota_consumptions (
                identity_type, identity_id, idempotency_key, consumed_units, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(identity_type, identity_id, idempotency_key) DO NOTHING
            """,
            (
                identity.identity_type,
                identity.identity_id,
                idempotency_key,
                max(1, int(consumed_units)),
                self.now_provider().isoformat(),
            ),
        )
        try:
            return int(cursor.rowcount or 0) == 1
        finally:
            cursor.close()

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        pool = self._pool
        if pool is None:
            raise RuntimeError("Worker conversion access pool is closed.")
        with pool.connection(timeout=self.db_pool_timeout_seconds) as conn:
            with conn.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{self.database_schema}", public')
            yield conn

    @staticmethod
    def _execute(conn, query: str, params: tuple = ()):
        cursor = conn.cursor()
        cursor.execute(query.replace("?", "%s"), params)
        return cursor

    @classmethod
    def _fetchone(cls, conn, query: str, params: tuple = ()):
        cursor = cls._execute(conn, query, params)
        try:
            return cursor.fetchone()
        finally:
            cursor.close()

    @staticmethod
    def _parse_usage_datetime(raw_value: str, fallback: datetime) -> datetime:
        try:
            parsed = datetime.fromisoformat(raw_value)
        except ValueError:
            return fallback
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _is_quota_window_expired(
        window_started_at: datetime,
        now: datetime,
        *,
        quota_window_days: int,
    ) -> bool:
        return now >= (window_started_at + timedelta(days=max(1, int(quota_window_days))))
