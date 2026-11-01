"""Preserve human-confirmed identities across automatic document recognition."""

import os

from alembic import op
from app.application.client_identity_schema_v1 import client_identity_schema_v1, protect_existing_client_identities

revision = "20261101_01"
down_revision = "20261031_01"
branch_labels = None
depends_on = None


def upgrade():
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    for statement in client_identity_schema_v1(schema):
        op.execute(statement)
    op.execute(protect_existing_client_identities(schema))


def downgrade():
    # Prefer application rollback with this additive table retained. A deliberate
    # schema downgrade removes only this revision's markers; export them first.
    schema = os.getenv("DATABASE_SCHEMA", "public").strip() or "public"
    client_identity_schema_v1(schema)
    op.execute(f'DROP TABLE IF EXISTS "{schema}".pro_client_identity_confirmations')
