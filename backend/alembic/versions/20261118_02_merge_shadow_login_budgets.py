"""Merge the independently deployable shadow and password-budget revisions."""

revision = "20261118_02"
down_revision = ("20261117_01", "20261118_01")
branch_labels = None
depends_on = None


def upgrade():
    # Both additive parent revisions own their tables; joining needs no DDL.
    pass


def downgrade():
    # Splitting the revision graph must not remove evidence or active budgets.
    pass
