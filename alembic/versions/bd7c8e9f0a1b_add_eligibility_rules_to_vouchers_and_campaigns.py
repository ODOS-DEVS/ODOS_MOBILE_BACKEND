"""Add eligibility_rules column to vouchers and campaigns

Revision ID: bd7c8e9f0a1b
Revises: ac6a722ceaab
Create Date: 2026-08-21 17:50:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "bd7c8e9f0a1b"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vouchers",
        sa.Column("eligibility_rules", sa.JSON(), nullable=True),
    )
    op.add_column(
        "merchandising_campaigns",
        sa.Column("eligibility_rules", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("merchandising_campaigns", "eligibility_rules")
    op.drop_column("vouchers", "eligibility_rules")
