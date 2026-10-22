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


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    """Add a column only when it is not already there.

    These columns exist on some databases without the matching alembic
    revision recorded — created out of band, most likely by a metadata
    create_all during early development. A bare `add_column` then raises
    DuplicateColumn, which aborts `upgrade head` *at this revision*, so every
    later migration silently never runs. That is how a missing `guide_videos`
    table and a run of 500s on unrelated endpoints trace back to one
    already-applied column.

    Guarding makes the migration safe to re-run against a database that is
    partially ahead of its own version stamp, which is the state any
    long-lived environment drifts into.
    """
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns(table)}
    if column.name not in existing:
        op.add_column(table, column)


def upgrade() -> None:
    _add_column_if_missing(
        "stores",
        sa.Column("is_on_vacation", sa.Boolean(), nullable=False, server_default="false"),
    )
    _add_column_if_missing(
        "stores",
        sa.Column("vacation_message", sa.String(length=500), nullable=True),
    )
    _add_column_if_missing(
        "stores",
        sa.Column("business_hours", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    _add_column_if_missing(
        "reviews",
        sa.Column("vendor_reply", sa.String(length=1000), nullable=True),
    )
    _add_column_if_missing(
        "reviews",
        sa.Column("vendor_replied_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reviews", "vendor_replied_at")
    op.drop_column("reviews", "vendor_reply")

    op.drop_column("stores", "business_hours")
    op.drop_column("stores", "vacation_message")
    op.drop_column("stores", "is_on_vacation")
