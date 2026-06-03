"""add user verified phones

Revision ID: e9a2b3c4d5f6
Revises: d8f3a1c2b4e5
Create Date: 2026-06-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9a2b3c4d5f6"
down_revision: Union[str, None] = "d8f3a1c2b4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_verified_phones",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column(
            "verified_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "phone", name="uq_user_verified_phone"),
    )
    op.create_index(
        op.f("ix_user_verified_phones_user_id"),
        "user_verified_phones",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_verified_phones_user_id"), table_name="user_verified_phones")
    op.drop_table("user_verified_phones")
