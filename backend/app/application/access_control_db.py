from __future__ import annotations

import sqlite3
import time
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


class AccessControlDbComponent:
    """Centralizes database connection and query helpers."""

    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def connect(self) -> AbstractContextManager[Any]:
        return self._connect()

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        service = self._service
        if service._use_postgres:
            assert service._postgres_module is not None and service._postgres_dict_row is not None
            last_exc: Exception | None = None
            for attempt in range(1, service.db_connect_retry_attempts + 1):
                try:
                    if service._postgres_pool is not None:
                        with service._postgres_pool.connection(timeout=service.db_pool_timeout_seconds) as conn:
                            with conn.cursor() as cur:
                                cur.execute(f'SET search_path TO "{service.database_schema}", public')
                            yield conn
                            return

                    with service._postgres_module.connect(
                        service.database_url,
                        row_factory=service._postgres_dict_row,
                    ) as conn:
                        with conn.cursor() as cur:
                            cur.execute(f'SET search_path TO "{service.database_schema}", public')
                        yield conn
                        return
                except Exception as exc:  # pragma: no cover - exercised in postgres environments
                    last_exc = exc
                    if attempt >= service.db_connect_retry_attempts or not self.is_retryable_db_exception(exc):
                        raise
                    sleep_seconds = (service.db_connect_retry_base_ms / 1000.0) * (2 ** (attempt - 1))
                    time.sleep(min(sleep_seconds, 2.0))

            if last_exc is not None:  # pragma: no cover - safety fallback
                raise last_exc
            raise RuntimeError("Failed to establish database connection.")

        conn = sqlite3.connect(service.db_file)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def execute(self, conn, query: str, params: tuple = ()):
        adapted = self.adapt_query(query)
        if self._service._use_postgres:
            cur = conn.cursor()
            cur.execute(adapted, params)
            return cur
        return conn.execute(adapted, params)

    def fetchone(self, conn, query: str, params: tuple = ()):
        cur = self.execute(conn, query, params)
        if self._service._use_postgres:
            try:
                return cur.fetchone()
            finally:
                cur.close()
        return cur.fetchone()

    def fetchall(self, conn, query: str, params: tuple = ()):
        cur = self.execute(conn, query, params)
        if self._service._use_postgres:
            try:
                return cur.fetchall()
            finally:
                cur.close()
        return cur.fetchall()

    def adapt_query(self, query: str) -> str:
        if self._service._use_postgres:
            return query.replace("?", "%s")
        return query

    def true_value(self):
        if self._service._use_postgres:
            return True
        return 1

    def false_value(self):
        if self._service._use_postgres:
            return False
        return 0

    def is_retryable_db_exception(self, exc: Exception) -> bool:
        message = str(exc).lower()
        retryable_hints = (
            "failed to acquire permit to connect",
            "too many database connection attempts",
            "connection timeout",
            "network is unreachable",
            "control plane request failed",
            "timeout expired",
            "could not connect",
            "connection refused",
        )
        return any(token in message for token in retryable_hints)
