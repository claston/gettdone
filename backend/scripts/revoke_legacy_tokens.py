"""Revoke retired user tokens without invalidating current cookie sessions.

Dry-run is the default. DATABASE_URL and DATABASE_SCHEMA are read from the
operator's environment; credentials and user records are never printed.
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone

REVOCATION_VERSION = 2026112501


def revoke_legacy_tokens(conn, *, schema, apply=False):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    table = f'"{schema}".users'
    before = conn.execute(f"SELECT count(*) FROM {table} WHERE credential_version < %s",
                          (REVOCATION_VERSION,)).fetchone()[0]
    changed = 0
    if apply:
        cursor = conn.execute(
            f"UPDATE {table} SET credential_version = %s, updated_at = %s WHERE credential_version < %s",
            (REVOCATION_VERSION, datetime.now(timezone.utc).isoformat(), REVOCATION_VERSION),
        )
        changed = cursor.rowcount
        conn.commit()
    return {"mode": "applied" if apply else "dry_run", "eligible_users": before,
            "changed_users": changed, "credential_version_floor": REVOCATION_VERSION,
            "cookie_sessions_changed": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit the idempotent credential-version bump")
    args = parser.parse_args()
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        parser.error("Set DATABASE_URL through the operator environment")
    import psycopg
    try:
        with psycopg.connect(url, connect_timeout=15) as conn:
            result = revoke_legacy_tokens(conn, schema=os.getenv("DATABASE_SCHEMA", "public"), apply=args.apply)
    except Exception as exc:
        # Driver messages can contain connection details. Keep output non-sensitive.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
