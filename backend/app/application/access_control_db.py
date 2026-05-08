from __future__ import annotations

from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.application.access_control import AccessControlService


class AccessControlDbComponent:
    """Thin adapter to centralize DB interaction entry points for future refactors."""

    def __init__(self, service: AccessControlService) -> None:
        self._service = service

    def connect(self) -> AbstractContextManager[Any]:
        return self._service._connect()

    def execute(self, conn, query: str, params: tuple = ()):
        return self._service._execute(conn, query, params)

    def fetchone(self, conn, query: str, params: tuple = ()):
        return self._service._fetchone(conn, query, params)

    def fetchall(self, conn, query: str, params: tuple = ()):
        return self._service._fetchall(conn, query, params)
