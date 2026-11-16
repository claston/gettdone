"""Add PRO fiscal evidence, classifications and allocations without changing cash."""

import os

from alembic import op
from app.application.pro_fiscal_schema import pro_fiscal_schema

revision = "20261116_01"
down_revision = "20261115_01"
branch_labels = None
depends_on = None


def upgrade():
    for statement in pro_fiscal_schema(os.getenv("DATABASE_SCHEMA", "public")):
        op.execute(statement)


def downgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public")
    pro_fiscal_schema(schema)  # Validate before interpolation.
    tables = ("pro_fiscal_imports", "pro_fiscal_links", "pro_movement_classifications", "pro_fiscal_operations", "pro_fiscal_documents")
    names = [f'"{schema}".{table}' for table in tables]
    # A pre-adoption rollback can remove empty objects, including their FKs to
    # older tables. After adoption retain all fiscal evidence for app rollback.
    # The lock makes the emptiness check and DDL atomic against late writers.
    populated = " OR ".join(f"EXISTS (SELECT 1 FROM {name})" for name in names)
    drop = " ".join(f"DROP TABLE {name};" for name in names)
    op.execute(f"""DO $$ BEGIN
        LOCK TABLE {", ".join(names)} IN ACCESS EXCLUSIVE MODE;
        IF NOT ({populated}) THEN {drop} END IF;
    END $$""")
