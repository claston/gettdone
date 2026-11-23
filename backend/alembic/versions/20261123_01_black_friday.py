"""Add private Black Friday offers without modifying existing contracts or schema."""
import os
import re

import sqlalchemy as sa

from alembic import op

revision = "20261123_01"
down_revision = "20261122_01"
branch_labels = None
depends_on = None


def upgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public") or "public"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError("Invalid DATABASE_SCHEMA")
    # Freeze historical commercial terms in the migration; never update old rows.
    for code, name, price, quota, period in (
        ("essencial_black", "Essencial Black", 1990, 600, "monthly"),
        ("profissional_black", "Profissional Black", 2990, 1000, "monthly"),
        ("black_friday_3_meses", "Black Friday 3 meses", 5990, 1000, "prepaid_quarter"),
    ):
        op.get_bind().execute(sa.text(f'''INSERT INTO "{schema}".plan_versions
            (id, code, name, version, currency, price_cents, billing_period, quota_mode, quota_limit,
             quota_window_days, max_upload_size_bytes, max_pages_per_file, max_pages_per_file_ocr,
             batch_conversion_enabled, batch_max_files, is_public, is_active, created_at)
            VALUES (:id, :code, :name, 1, 'BRL', :price, :period, 'pages', :quota,
                    30, 10485760, 100, 6, TRUE, 12, FALSE, TRUE, '2026-11-23T00:00:00+00:00')
            ON CONFLICT(code, version) DO NOTHING'''),
            dict(id=f"plan_{code}_v1", code=code, name=name, price=price, period=period, quota=quota))


def downgrade():
    # Retain private rows referenced by orders and subscriptions. They are hidden
    # from old catalogs. After sales, retain the expiry-aware API/worker runtime.
    pass
