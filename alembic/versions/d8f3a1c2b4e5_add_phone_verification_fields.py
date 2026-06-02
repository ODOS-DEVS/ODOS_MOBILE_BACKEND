"""add phone verification fields

Revision ID: d8f3a1c2b4e5
Revises: c2b7e1a9f4d3
Create Date: 2026-06-02 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8f3a1c2b4e5"
down_revision: Union[str, Sequence[str], None] = "c2b7e1a9f4d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "phone_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "users",
        sa.Column("phone_verification_code_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("phone_verification_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("phone_verification_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("phone_verification_phone", sa.String(length=30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "phone_verification_phone")
    op.drop_column("users", "phone_verification_sent_at")
    op.drop_column("users", "phone_verification_expires_at")
    op.drop_column("users", "phone_verification_code_hash")
    op.drop_column("users", "phone_verified")
