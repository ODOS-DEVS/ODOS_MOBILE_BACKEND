"""add password reset fields to users

Revision ID: 59fc33f93bb9
Revises: f6b6f808f8d9
Create Date: 2026-04-28 12:25:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "59fc33f93bb9"
down_revision: Union[str, None] = "f6b6f808f8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_reset_code_hash", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("password_reset_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("password_reset_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "password_reset_sent_at")
    op.drop_column("users", "password_reset_expires_at")
    op.drop_column("users", "password_reset_code_hash")
