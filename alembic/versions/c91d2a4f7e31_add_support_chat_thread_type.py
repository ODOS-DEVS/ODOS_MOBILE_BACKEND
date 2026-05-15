"""add support chat thread type

Revision ID: c91d2a4f7e31
Revises: b7e1c2f4a9d3
Create Date: 2026-05-14 11:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c91d2a4f7e31"
down_revision = "b7e1c2f4a9d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    chat_thread_type = sa.Enum("store", "support", name="chat_thread_type")
    chat_thread_type.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "chat_threads",
        sa.Column(
            "thread_type",
            chat_thread_type,
            server_default="store",
            nullable=False,
        ),
    )
    op.add_column("chat_threads", sa.Column("subject", sa.String(length=255), nullable=True))
    op.create_index(
        op.f("ix_chat_threads_thread_type"),
        "chat_threads",
        ["thread_type"],
        unique=False,
    )

    op.drop_constraint(
        "uq_chat_threads_customer_store",
        "chat_threads",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_chat_threads_customer_store_type",
        "chat_threads",
        ["customer_user_id", "store_id", "thread_type"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_chat_threads_customer_store_type",
        "chat_threads",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_chat_threads_customer_store",
        "chat_threads",
        ["customer_user_id", "store_id"],
    )

    op.drop_index(op.f("ix_chat_threads_thread_type"), table_name="chat_threads")
    op.drop_column("chat_threads", "subject")
    op.drop_column("chat_threads", "thread_type")

    chat_thread_type = sa.Enum("store", "support", name="chat_thread_type")
    chat_thread_type.drop(op.get_bind(), checkfirst=True)
