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


def _has_return_request_id() -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(
        col["name"] == "return_request_id"
        for col in inspector.get_columns("customer_wallet_transactions")
    )


def upgrade() -> None:
    # 0a1b838e2b4a adds this column upstream. Databases stamped past that
    # revision without having run it still need the column, so add it only
    # when absent rather than assuming either history.
    if not _has_return_request_id():
        op.add_column(
            "customer_wallet_transactions",
            sa.Column("return_request_id", sa.UUID(), nullable=True),
        )


def downgrade() -> None:
    if _has_return_request_id():
        op.drop_column("customer_wallet_transactions", "return_request_id")
