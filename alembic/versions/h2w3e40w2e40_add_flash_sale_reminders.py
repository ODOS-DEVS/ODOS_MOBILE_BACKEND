"""Add flash_sale_reminders: shoppers asking to be told when a sale starts.

Revision ID: h2w3e40w2e40
Revises: g1v2d30v1d30
Create Date: 2026-11-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "h2w3e40w2e40"
down_revision = "g1v2d30v1d30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "flash_sale_reminders" in inspector.get_table_names():
        return
    op.create_table(
        "flash_sale_reminders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("flash_sale_events.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("event_id", "user_id", name="uq_flash_sale_reminder_event_user"),
    )


def downgrade() -> None:
    op.drop_table("flash_sale_reminders")
