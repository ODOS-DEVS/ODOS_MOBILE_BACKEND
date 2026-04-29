"""add email verification fields to users

Revision ID: f6b6f808f8d9
Revises: 0d6f0d0a7c43
Create Date: 2026-04-28 12:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f6b6f808f8d9"
down_revision: Union[str, None] = "0d6f0d0a7c43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verification_code_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("email_verification_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("email_verification_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "email_verification_sent_at")
    op.drop_column("users", "email_verification_expires_at")
    op.drop_column("users", "email_verification_code_hash")
