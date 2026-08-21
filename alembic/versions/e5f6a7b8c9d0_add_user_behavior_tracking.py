"""add user behavior tracking

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "personalization_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "analytics_enabled",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
    )

    op.create_table(
        "user_behavior_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=True),
        sa.Column("store_id", sa.String(length=50), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("search_query", sa.String(length=255), nullable=True),
        sa.Column("source_screen", sa.String(length=80), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_behavior_events_created_at"),
        "user_behavior_events",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_behavior_events_event_type"),
        "user_behavior_events",
        ["event_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_behavior_events_product_id"),
        "user_behavior_events",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_behavior_events_session_id"),
        "user_behavior_events",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_behavior_events_store_id"),
        "user_behavior_events",
        ["store_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_behavior_events_user_id"),
        "user_behavior_events",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_behavior_events_user_created",
        "user_behavior_events",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_user_behavior_events_user_type",
        "user_behavior_events",
        ["user_id", "event_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_user_behavior_events_user_type", table_name="user_behavior_events")
    op.drop_index("ix_user_behavior_events_user_created", table_name="user_behavior_events")
    op.drop_index(op.f("ix_user_behavior_events_user_id"), table_name="user_behavior_events")
    op.drop_index(op.f("ix_user_behavior_events_store_id"), table_name="user_behavior_events")
    op.drop_index(op.f("ix_user_behavior_events_session_id"), table_name="user_behavior_events")
    op.drop_index(op.f("ix_user_behavior_events_product_id"), table_name="user_behavior_events")
    op.drop_index(op.f("ix_user_behavior_events_event_type"), table_name="user_behavior_events")
    op.drop_index(op.f("ix_user_behavior_events_created_at"), table_name="user_behavior_events")
    op.drop_table("user_behavior_events")
    op.drop_column("users", "analytics_enabled")
    op.drop_column("users", "personalization_enabled")
