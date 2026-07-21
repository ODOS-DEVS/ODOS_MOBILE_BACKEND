"""Add Seller Center Wave 1 fields: store vacation/hours and review replies.

Revision ID: s9t0u1v2w3x4
Revises: r8s9t0u1v2w3
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "s9t0u1v2w3x4"
down_revision = "r8s9t0u1v2w3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stores",
        sa.Column("is_on_vacation", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "stores",
        sa.Column("vacation_message", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "stores",
        sa.Column("business_hours", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.add_column(
        "reviews",
        sa.Column("vendor_reply", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "reviews",
        sa.Column("vendor_replied_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reviews", "vendor_replied_at")
    op.drop_column("reviews", "vendor_reply")

    op.drop_column("stores", "business_hours")
    op.drop_column("stores", "vacation_message")
    op.drop_column("stores", "is_on_vacation")
