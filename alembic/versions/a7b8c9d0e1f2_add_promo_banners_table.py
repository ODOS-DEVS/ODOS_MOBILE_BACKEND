"""add promo banners table

Revision ID: a7b8c9d0e1f2
Revises: e9a2b3c4d5f6
Create Date: 2026-06-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "e9a2b3c4d5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "promo_banners",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("subtitle", sa.String(length=255), nullable=True),
        sa.Column(
            "cta_label",
            sa.String(length=80),
            server_default="Browse deals",
            nullable=False,
        ),
        sa.Column("cta_link", sa.String(length=500), nullable=True),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("accent", sa.String(length=20), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_promo_banners_sort_order", "promo_banners", ["sort_order"])
    op.create_index("ix_promo_banners_is_active", "promo_banners", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_promo_banners_is_active", table_name="promo_banners")
    op.drop_index("ix_promo_banners_sort_order", table_name="promo_banners")
    op.drop_table("promo_banners")
