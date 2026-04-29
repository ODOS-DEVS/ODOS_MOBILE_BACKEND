"""add notification preferences to users

Revision ID: ef42a7c13b5e
Revises: c38f2a8b9d11
Create Date: 2026-04-29 20:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ef42a7c13b5e"
down_revision: Union[str, Sequence[str], None] = "c38f2a8b9d11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("allow_notifications", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "users",
        sa.Column("discount_notifications", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        "users",
        sa.Column("store_notifications", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "users",
        sa.Column("system_notifications", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "users",
        sa.Column("location_notifications", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "users",
        sa.Column("location_updates", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column("users", "location_updates")
    op.drop_column("users", "location_notifications")
    op.drop_column("users", "system_notifications")
    op.drop_column("users", "store_notifications")
    op.drop_column("users", "discount_notifications")
    op.drop_column("users", "allow_notifications")
