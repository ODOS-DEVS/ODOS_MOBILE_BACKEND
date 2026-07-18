"""add assistant conversations, messages, and feedback

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-07-06 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "l2m3n4o5p6q7"
down_revision = "k1l2m3n4o5p6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    # Production may already have these tables while alembic_version was
    # recovered to an earlier revision (e.g. stamped back to k1l2m3n4o5p6).
    if "assistant_conversations" not in existing:
        op.create_table(
            "assistant_conversations",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("screen", sa.String(length=80), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_assistant_conversations_user_id"),
            "assistant_conversations",
            ["user_id"],
            unique=False,
        )

    if "assistant_messages" not in existing:
        op.create_table(
            "assistant_messages",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["conversation_id"],
                ["assistant_conversations.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_assistant_messages_conversation_id"),
            "assistant_messages",
            ["conversation_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_assistant_messages_created_at"),
            "assistant_messages",
            ["created_at"],
            unique=False,
        )

    if "assistant_message_feedback" not in existing:
        op.create_table(
            "assistant_message_feedback",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("comment", sa.String(length=500), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["message_id"], ["assistant_messages.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("message_id", "user_id", name="uq_assistant_feedback_message_user"),
        )
        op.create_index(
            op.f("ix_assistant_message_feedback_message_id"),
            "assistant_message_feedback",
            ["message_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_assistant_message_feedback_user_id"),
            "assistant_message_feedback",
            ["user_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    existing = set(inspector.get_table_names())

    if "assistant_message_feedback" in existing:
        op.drop_index(
            op.f("ix_assistant_message_feedback_user_id"),
            table_name="assistant_message_feedback",
        )
        op.drop_index(
            op.f("ix_assistant_message_feedback_message_id"),
            table_name="assistant_message_feedback",
        )
        op.drop_table("assistant_message_feedback")
    if "assistant_messages" in existing:
        op.drop_index(op.f("ix_assistant_messages_created_at"), table_name="assistant_messages")
        op.drop_index(
            op.f("ix_assistant_messages_conversation_id"),
            table_name="assistant_messages",
        )
        op.drop_table("assistant_messages")
    if "assistant_conversations" in existing:
        op.drop_index(
            op.f("ix_assistant_conversations_user_id"),
            table_name="assistant_conversations",
        )
        op.drop_table("assistant_conversations")
