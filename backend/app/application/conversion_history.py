from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable


def record_user_conversion(
    conn,
    *,
    execute: Callable,
    now_iso: str,
    user_id: str,
    processing_id: str,
    filename: str,
    model: str,
    conversion_type: str,
    status: str,
    transactions_count: int | None,
    pages_count: int | None = None,
    created_at: str | None = None,
    expires_at: str | None = None,
) -> None:
    execute(
        conn,
        """
        INSERT INTO user_conversions (
          analysis_id,
          user_id,
          created_at,
          expires_at,
          filename,
          model,
          conversion_type,
          status,
          transactions_count,
          pages_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(analysis_id)
        DO UPDATE SET
          user_id=excluded.user_id,
          created_at=excluded.created_at,
          expires_at=excluded.expires_at,
          filename=excluded.filename,
          model=excluded.model,
          conversion_type=excluded.conversion_type,
          status=excluded.status,
          transactions_count=excluded.transactions_count,
          pages_count=excluded.pages_count
        """,
        (
            processing_id,
            user_id,
            created_at or now_iso,
            expires_at,
            filename.strip() or f"{processing_id}.pdf",
            model.strip() or "Nao identificado",
            conversion_type.strip() or "pdf-ofx",
            status.strip() or "Sucesso",
            transactions_count,
            pages_count,
        ),
    )


def list_user_conversions(
    conn,
    *,
    fetchall: Callable,
    now_provider: Callable[[], datetime],
    user_id: str,
    limit: int = 20,
) -> list[dict[str, str | int]]:
    rows = fetchall(
        conn,
        """
        SELECT
          analysis_id,
          created_at,
          expires_at,
          filename,
          model,
          conversion_type,
          status,
          transactions_count,
          pages_count
        FROM user_conversions
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, max(1, min(limit, 100))),
    )

    now = now_provider()
    items: list[dict[str, str | int]] = []
    for row in rows:
        status = str(row["status"] or "Sucesso")
        expires_at = str(row["expires_at"] or "").strip()
        if expires_at and _is_expired(expires_at, now):
            status = "Expirado"
        item: dict[str, str | int] = {
            "processing_id": str(row["analysis_id"]),
            "created_at": str(row["created_at"]),
            "filename": str(row["filename"]),
            "model": str(row["model"]),
            "conversion_type": str(row["conversion_type"]),
            "status": status,
        }
        tx_count = row["transactions_count"]
        if isinstance(tx_count, int):
            item["transactions_count"] = tx_count
        page_count = row["pages_count"]
        if isinstance(page_count, int):
            item["pages_count"] = page_count
        items.append(item)
    return items


def _is_expired(expires_at_raw: str, now: datetime) -> bool:
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now
