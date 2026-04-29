"""add notification events table

Revision ID: cc7d2ea1b6a4
Revises: b91f7ae2c4f3
Create Date: 2026-04-29 21:35:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "cc7d2ea1b6a4"
down_revision: Union[str, Sequence[str], None] = "b91f7ae2c4f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=60), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("body", sa.String(length=500), nullable=False),
        sa.Column("icon", sa.String(length=60), nullable=False),
        sa.Column("accent", sa.String(length=30), nullable=False),
        sa.Column("action_label", sa.String(length=80), nullable=True),
        sa.Column("route_type", sa.String(length=40), nullable=True),
        sa.Column("route_target_id", sa.String(length=120), nullable=True),
        sa.Column("image_key", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_notification_events_user_id"), "notification_events", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notification_events_user_id"), table_name="notification_events")
    op.drop_table("notification_events")
