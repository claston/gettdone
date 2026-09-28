"""version marketing consent

Revision ID: 20260927_01
Revises: 20260914_01
Create Date: 2026-09-27 20:00:00
"""

from __future__ import annotations

import os
import re
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260927_01"
down_revision: Union[str, Sequence[str], None] = "20260914_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _schema() -> str:
    raw = (os.getenv("DATABASE_SCHEMA", "public") or "").strip()
    if not raw:
        return "public"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", raw):
        raise RuntimeError("DATABASE_SCHEMA must be a valid PostgreSQL schema name.")
    return raw


def upgrade() -> None:
    schema = _schema()
    op.execute(
        f'ALTER TABLE "{schema}".users '
        "ADD COLUMN IF NOT EXISTS product_updates_consent_version INTEGER"
    )
    op.execute(
        f'UPDATE "{schema}".users SET product_updates_consent_version = 1 '
        "WHERE product_updates_opt_in = TRUE AND product_updates_consent_version IS NULL"
    )


def downgrade() -> None:
    schema = _schema()
    op.execute(
        f'ALTER TABLE "{schema}".users '
        "DROP COLUMN IF EXISTS product_updates_consent_version"
    )
