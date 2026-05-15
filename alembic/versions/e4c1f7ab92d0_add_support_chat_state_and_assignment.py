"""add support chat state and assignment

Revision ID: e4c1f7ab92d0
Revises: c91d2a4f7e31
Create Date: 2026-05-14 20:45:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "e4c1f7ab92d0"
down_revision = "c91d2a4f7e31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    support_chat_status = sa.Enum(
        "waiting_on_admin",
        "waiting_on_customer",
        "resolved",
        name="support_chat_status",
    )
    support_chat_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "chat_threads",
        sa.Column("support_status", support_chat_status, nullable=True),
    )
    op.add_column(
        "chat_threads",
        sa.Column(
            "assigned_admin_user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "chat_threads",
        sa.Column("assigned_admin_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "chat_threads",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_chat_threads_assigned_admin_user_id_users",
        "chat_threads",
        "users",
        ["assigned_admin_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_chat_threads_support_status"),
        "chat_threads",
        ["support_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_chat_threads_assigned_admin_user_id"),
        "chat_threads",
        ["assigned_admin_user_id"],
        unique=False,
    )

    op.execute(
        """
        UPDATE chat_threads
        SET support_status = 'waiting_on_admin'
        WHERE thread_type = 'support'
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_chat_threads_assigned_admin_user_id"), table_name="chat_threads")
    op.drop_index(op.f("ix_chat_threads_support_status"), table_name="chat_threads")
    op.drop_constraint(
        "fk_chat_threads_assigned_admin_user_id_users",
        "chat_threads",
        type_="foreignkey",
    )
    op.drop_column("chat_threads", "resolved_at")
    op.drop_column("chat_threads", "assigned_admin_at")
    op.drop_column("chat_threads", "assigned_admin_user_id")
    op.drop_column("chat_threads", "support_status")

    support_chat_status = sa.Enum(
        "waiting_on_admin",
        "waiting_on_customer",
        "resolved",
        name="support_chat_status",
    )
    support_chat_status.drop(op.get_bind(), checkfirst=True)
