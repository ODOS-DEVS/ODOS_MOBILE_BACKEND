"""enhance promotions system

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-15

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "vouchers",
        sa.Column("campaign_tag", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column(
            "visibility",
            sa.String(length=20),
            server_default="public",
            nullable=False,
        ),
    )
    op.add_column(
        "vouchers",
        sa.Column(
            "approval_status",
            sa.String(length=20),
            server_default="approved",
            nullable=False,
        ),
    )
    op.add_column(
        "vouchers",
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("review_notes", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column(
            "first_order_only",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "vouchers",
        sa.Column(
            "new_user_only",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "vouchers",
        sa.Column("category_slugs", postgresql.ARRAY(sa.String(length=80)), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("product_ids", postgresql.ARRAY(sa.String(length=100)), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("excluded_product_ids", postgresql.ARRAY(sa.String(length=100)), nullable=True),
    )
    op.create_index(op.f("ix_vouchers_approval_status"), "vouchers", ["approval_status"], unique=False)
    op.create_index(op.f("ix_vouchers_campaign_tag"), "vouchers", ["campaign_tag"], unique=False)

    op.add_column(
        "products",
        sa.Column("sale_starts_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("sale_ends_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("regular_price", sa.Integer(), nullable=True),
    )

    op.add_column(
        "flash_sale_event_products",
        sa.Column("flash_sale_price", sa.Integer(), nullable=True),
    )
    op.add_column(
        "flash_sale_event_products",
        sa.Column("flash_sale_old_price", sa.Integer(), nullable=True),
    )

    op.add_column(
        "promo_banners",
        sa.Column("campaign_tag", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "promo_banners",
        sa.Column(
            "link_type",
            sa.String(length=30),
            server_default="screen",
            nullable=False,
        ),
    )

    op.create_table(
        "flash_sale_nominations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("proposed_price", sa.Integer(), nullable=True),
        sa.Column("proposed_old_price", sa.Integer(), nullable=True),
        sa.Column("stock_limit", sa.Integer(), nullable=True),
        sa.Column("max_per_user", sa.Integer(), nullable=True),
        sa.Column("vendor_note", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("review_notes", sa.String(length=255), nullable=True),
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
        sa.ForeignKeyConstraint(["event_id"], ["flash_sale_events.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vendor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_flash_sale_nominations_event_id"),
        "flash_sale_nominations",
        ["event_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_flash_sale_nominations_product_id"),
        "flash_sale_nominations",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_flash_sale_nominations_status"),
        "flash_sale_nominations",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_flash_sale_nominations_vendor_user_id"),
        "flash_sale_nominations",
        ["vendor_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_flash_sale_nominations_vendor_user_id"), table_name="flash_sale_nominations")
    op.drop_index(op.f("ix_flash_sale_nominations_status"), table_name="flash_sale_nominations")
    op.drop_index(op.f("ix_flash_sale_nominations_product_id"), table_name="flash_sale_nominations")
    op.drop_index(op.f("ix_flash_sale_nominations_event_id"), table_name="flash_sale_nominations")
    op.drop_table("flash_sale_nominations")

    op.drop_column("promo_banners", "link_type")
    op.drop_column("promo_banners", "campaign_tag")

    op.drop_column("flash_sale_event_products", "flash_sale_old_price")
    op.drop_column("flash_sale_event_products", "flash_sale_price")

    op.drop_column("products", "regular_price")
    op.drop_column("products", "sale_ends_at")
    op.drop_column("products", "sale_starts_at")

    op.drop_index(op.f("ix_vouchers_campaign_tag"), table_name="vouchers")
    op.drop_index(op.f("ix_vouchers_approval_status"), table_name="vouchers")
    op.drop_column("vouchers", "excluded_product_ids")
    op.drop_column("vouchers", "product_ids")
    op.drop_column("vouchers", "category_slugs")
    op.drop_column("vouchers", "new_user_only")
    op.drop_column("vouchers", "first_order_only")
    op.drop_column("vouchers", "review_notes")
    op.drop_column("vouchers", "reviewed_by_user_id")
    op.drop_column("vouchers", "created_by_user_id")
    op.drop_column("vouchers", "approval_status")
    op.drop_column("vouchers", "visibility")
    op.drop_column("vouchers", "campaign_tag")
