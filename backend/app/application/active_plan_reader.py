"""Read-only subscription query shared by API and worker runtimes."""

from datetime import datetime, timezone
from typing import Callable


def read_active_user_plan(
    conn,
    *,
    fetchone: Callable,
    user_id: str,
    now: datetime | None = None,
) -> dict[str, str | int] | None:
    row = fetchone(
        conn,
        """
        SELECT
          pv.code,
          pv.name,
          pv.version,
          pv.quota_mode,
          pv.quota_limit,
          pv.quota_window_days,
          pv.max_upload_size_bytes,
          pv.max_pages_per_file,
          pv.max_pages_per_file_ocr,
          pv.batch_conversion_enabled,
          pv.batch_max_files,
          ups.started_at,
          ups.ended_at
        FROM user_plan_subscriptions ups
        JOIN plan_versions pv ON pv.id = ups.plan_version_id
        WHERE ups.user_id = ? AND ups.status = 'active'
          AND (ups.ended_at IS NULL OR ups.ended_at > ?)
          AND (pv.code <> 'escritorio_documentos' OR ups.ended_at IS NOT NULL)
        ORDER BY pv.version DESC
        LIMIT 1
        """,
        (user_id, (now or datetime.now(timezone.utc)).isoformat()),
    )
    if row is None:
        return None
    return {
        "code": str(row["code"]),
        "name": str(row["name"]),
        "version": int(row["version"]),
        "quota_mode": str(row["quota_mode"]),
        "quota_limit": int(row["quota_limit"]),
        "quota_window_days": int(row["quota_window_days"]),
        "max_upload_size_bytes": int(row["max_upload_size_bytes"]),
        "max_pages_per_file": int(row["max_pages_per_file"]),
        "max_pages_per_file_ocr": int(row["max_pages_per_file_ocr"]),
        "batch_conversion_enabled": bool(row["batch_conversion_enabled"]),
        "batch_max_files": int(row["batch_max_files"]),
        "started_at": str(row["started_at"]),
        "ended_at": str(row["ended_at"]) if row["ended_at"] else None,
    }
