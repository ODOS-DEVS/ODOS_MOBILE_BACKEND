"""add review moderation fields

Revision ID: 1c3e9f6a4b72
Revises: d2a7f91ce441
Create Date: 2026-05-12 21:05:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "1c3e9f6a4b72"
down_revision = "d2a7f91ce441"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reviews",
        sa.Column("is_hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "reviews",
        sa.Column("moderation_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "reviews",
        sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "reviews",
        sa.Column("moderated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(op.f("ix_reviews_moderated_by_user_id"), "reviews", ["moderated_by_user_id"], unique=False)
    op.create_foreign_key(
        "fk_reviews_moderated_by_user_id_users",
        "reviews",
        "users",
        ["moderated_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_reviews_moderated_by_user_id_users", "reviews", type_="foreignkey")
    op.drop_index(op.f("ix_reviews_moderated_by_user_id"), table_name="reviews")
    op.drop_column("reviews", "moderated_by_user_id")
    op.drop_column("reviews", "moderated_at")
    op.drop_column("reviews", "moderation_reason")
    op.drop_column("reviews", "is_hidden")
