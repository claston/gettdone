from __future__ import annotations

from typing import Callable


def record_anonymous_conversion_event(
    conn,
    *,
    execute: Callable,
    event_id: str,
    created_at: str,
    anonymous_fingerprint: str,
    filename: str,
    model: str,
    conversion_type: str,
    status: str,
    transactions_count: int | None,
    pages_count: int | None,
    scanned_likely: bool | None,
    ocr_used: bool,
    ocr_pages_processed: int,
    duration_ms: int,
    error_code: str | None = None,
) -> None:
    execute(
        conn,
        """
        INSERT INTO anonymous_conversion_events (
          id,
          created_at,
          anonymous_fingerprint,
          filename,
          model,
          conversion_type,
          status,
          transactions_count,
          pages_count,
          scanned_likely,
          ocr_used,
          ocr_pages_processed,
          duration_ms,
          error_code
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id)
        DO UPDATE SET
          created_at=excluded.created_at,
          anonymous_fingerprint=excluded.anonymous_fingerprint,
          filename=excluded.filename,
          model=excluded.model,
          conversion_type=excluded.conversion_type,
          status=excluded.status,
          transactions_count=excluded.transactions_count,
          pages_count=excluded.pages_count,
          scanned_likely=excluded.scanned_likely,
          ocr_used=excluded.ocr_used,
          ocr_pages_processed=excluded.ocr_pages_processed,
          duration_ms=excluded.duration_ms,
          error_code=excluded.error_code
        """,
        (
            event_id,
            created_at,
            anonymous_fingerprint.strip() or "unknown",
            filename.strip() or "unknown.pdf",
            model.strip() or "Nao identificado",
            conversion_type.strip() or "pdf-ofx",
            status.strip() or "Sucesso",
            transactions_count,
            pages_count,
            scanned_likely,
            bool(ocr_used),
            max(0, int(ocr_pages_processed or 0)),
            max(0, int(duration_ms or 0)),
            (error_code or "").strip() or None,
        ),
    )
