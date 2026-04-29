"""add notification reads and push token

Revision ID: b91f7ae2c4f3
Revises: ef42a7c13b5e
Create Date: 2026-04-29 21:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "b91f7ae2c4f3"
down_revision: Union[str, Sequence[str], None] = "ef42a7c13b5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("expo_push_token", sa.String(length=255), nullable=True))

    op.create_table(
        "notification_reads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("notification_key", sa.String(length=120), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "notification_key", name="uq_notification_reads_user_id_notification_key"),
    )
    op.create_index(op.f("ix_notification_reads_user_id"), "notification_reads", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_notification_reads_user_id"), table_name="notification_reads")
    op.drop_table("notification_reads")
    op.drop_column("users", "expo_push_token")
