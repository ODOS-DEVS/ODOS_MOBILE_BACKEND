"""Add return_request_id column to customer wallet transactions

Revision ID: ce8d9f0a1b2c
Revises: bd7c8e9f0a1b
Create Date: 2026-08-21 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ce8d9f0a1b2c"
down_revision: Union[str, Sequence[str], None] = "bd7c8e9f0a1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "customer_wallet_transactions",
        sa.Column("return_request_id", sa.UUID(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("customer_wallet_transactions", "return_request_id")
