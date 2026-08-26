"""Add eligibility_rules column to vouchers and campaigns

Revision ID: bd7c8e9f0a1b
Revises: 0a1b838e2b4a
Create Date: 2026-08-21 17:50:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bd7c8e9f0a1b"
down_revision: Union[str, Sequence[str], None] = "0a1b838e2b4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return True  # nothing to add to a table that does not exist yet
    return any(col["name"] == column for col in inspector.get_columns(table))


def upgrade() -> None:
    # Guarded so the revision is idempotent: databases that were stamped past
    # it without running it (see app/core/alembic_recovery.py) can be repaired
    # by re-running, and databases that already have the columns no-op.
    for table in ("vouchers", "merchandising_campaigns"):
        if not _has_column(table, "eligibility_rules"):
            op.add_column(table, sa.Column("eligibility_rules", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in ("merchandising_campaigns", "vouchers"):
        if _has_column(table, "eligibility_rules"):
            op.drop_column(table, "eligibility_rules")
