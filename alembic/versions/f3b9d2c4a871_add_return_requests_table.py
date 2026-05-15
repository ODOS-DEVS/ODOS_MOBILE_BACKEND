"""add return requests table

Revision ID: f3b9d2c4a871
Revises: e4c1f7ab92d0
Create Date: 2026-05-15 12:35:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "f3b9d2c4a871"
down_revision = "e4c1f7ab92d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "return_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="requested"),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=160), nullable=False),
        sa.Column("details", sa.String(length=1000), nullable=True),
        sa.Column("admin_note", sa.String(length=1000), nullable=True),
        sa.Column("refund_amount", sa.Float(), nullable=True),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_return_requests_order_id"), "return_requests", ["order_id"], unique=False)
    op.create_index(op.f("ix_return_requests_order_item_id"), "return_requests", ["order_item_id"], unique=False)
    op.create_index(op.f("ix_return_requests_user_id"), "return_requests", ["user_id"], unique=False)
    op.create_index(op.f("ix_return_requests_request_type"), "return_requests", ["request_type"], unique=False)
    op.create_index(op.f("ix_return_requests_status"), "return_requests", ["status"], unique=False)
    op.create_index(
        op.f("ix_return_requests_reviewed_by_user_id"),
        "return_requests",
        ["reviewed_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_return_requests_reviewed_by_user_id"), table_name="return_requests")
    op.drop_index(op.f("ix_return_requests_status"), table_name="return_requests")
    op.drop_index(op.f("ix_return_requests_request_type"), table_name="return_requests")
    op.drop_index(op.f("ix_return_requests_user_id"), table_name="return_requests")
    op.drop_index(op.f("ix_return_requests_order_item_id"), table_name="return_requests")
    op.drop_index(op.f("ix_return_requests_order_id"), table_name="return_requests")
    op.drop_table("return_requests")
