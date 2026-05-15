"""add chat tables

Revision ID: b7e1c2f4a9d3
Revises: 1c3e9f6a4b72
Create Date: 2026-05-13 13:10:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b7e1c2f4a9d3"
down_revision = "1c3e9f6a4b72"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_threads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", sa.String(length=50), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=True),
        sa.Column("product_title", sa.String(length=255), nullable=True),
        sa.Column("product_image_url", sa.String(length=500), nullable=True),
        sa.Column("last_message_text", sa.String(length=2000), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["customer_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vendor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "customer_user_id",
            "store_id",
            name="uq_chat_threads_customer_store",
        ),
    )
    op.create_index(op.f("ix_chat_threads_customer_user_id"), "chat_threads", ["customer_user_id"], unique=False)
    op.create_index(op.f("ix_chat_threads_store_id"), "chat_threads", ["store_id"], unique=False)
    op.create_index(op.f("ix_chat_threads_vendor_user_id"), "chat_threads", ["vendor_user_id"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column(
            "is_read",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_messages_recipient_user_id"), "chat_messages", ["recipient_user_id"], unique=False)
    op.create_index(op.f("ix_chat_messages_sender_user_id"), "chat_messages", ["sender_user_id"], unique=False)
    op.create_index(op.f("ix_chat_messages_thread_id"), "chat_messages", ["thread_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_messages_thread_id"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_sender_user_id"), table_name="chat_messages")
    op.drop_index(op.f("ix_chat_messages_recipient_user_id"), table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_chat_threads_vendor_user_id"), table_name="chat_threads")
    op.drop_index(op.f("ix_chat_threads_store_id"), table_name="chat_threads")
    op.drop_index(op.f("ix_chat_threads_customer_user_id"), table_name="chat_threads")
    op.drop_table("chat_threads")
