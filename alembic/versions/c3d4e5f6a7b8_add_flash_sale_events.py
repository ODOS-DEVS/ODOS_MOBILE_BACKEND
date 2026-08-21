"""add flash sale events

Revision ID: c3d4e5f6a7b8
Revises: a7b8c9d0e1f2
Create Date: 2026-06-02

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "aaabbbcccddd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flash_sale_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("subtitle", sa.String(length=255), nullable=True),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
    op.create_index("ix_flash_sale_events_slug", "flash_sale_events", ["slug"], unique=True)
    op.create_index("ix_flash_sale_events_sort_order", "flash_sale_events", ["sort_order"])
    op.create_index("ix_flash_sale_events_is_active", "flash_sale_events", ["is_active"])

    op.create_table(
        "flash_sale_event_products",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["flash_sale_events.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "product_id"),
    )
    op.create_index(
        "ix_flash_sale_event_products_product_id",
        "flash_sale_event_products",
        ["product_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_flash_sale_event_products_product_id", table_name="flash_sale_event_products")
    op.drop_table("flash_sale_event_products")
    op.drop_index("ix_flash_sale_events_is_active", table_name="flash_sale_events")
    op.drop_index("ix_flash_sale_events_sort_order", table_name="flash_sale_events")
    op.drop_index("ix_flash_sale_events_slug", table_name="flash_sale_events")
    op.drop_table("flash_sale_events")
