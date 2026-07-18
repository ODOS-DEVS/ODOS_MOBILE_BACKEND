"""Add merchandising campaigns as first-class marketing entities.

Revision ID: r8s9t0u1v2w3
Revises: q7r8s9t0u1v2
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "r8s9t0u1v2w3"
down_revision = "q7r8s9t0u1v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchandising_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("subtitle", sa.String(length=255), nullable=True),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("banner_image_url", sa.String(length=500), nullable=True),
        sa.Column("thumbnail_image_url", sa.String(length=500), nullable=True),
        sa.Column("icon_key", sa.String(length=80), nullable=True),
        sa.Column("theme_color", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("visibility", sa.String(length=20), nullable=False, server_default="public"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("display_priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_products", sa.Integer(), nullable=True),
        sa.Column("product_sort", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("hide_out_of_stock", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "include_entire_marketplace",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("allow_vendor_opt_in", sa.Boolean(), nullable=False, server_default="false"),
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
    )
    op.create_index("ix_merchandising_campaigns_slug", "merchandising_campaigns", ["slug"], unique=True)
    op.create_index("ix_merchandising_campaigns_status", "merchandising_campaigns", ["status"])
    op.create_index(
        "ix_merchandising_campaigns_featured_priority",
        "merchandising_campaigns",
        ["is_featured", "display_priority"],
    )

    op.create_table(
        "merchandising_campaign_products",
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["merchandising_campaigns.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("campaign_id", "product_id"),
    )

    op.create_table(
        "merchandising_campaign_categories",
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_slug", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["merchandising_campaigns.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("campaign_id", "category_slug"),
    )

    op.create_table(
        "merchandising_campaign_stores",
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_id", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["merchandising_campaigns.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("campaign_id", "store_id"),
    )

    op.create_table(
        "merchandising_campaign_opt_ins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("review_notes", sa.String(length=255), nullable=True),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["merchandising_campaigns.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vendor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_merchandising_campaign_opt_ins_campaign_id",
        "merchandising_campaign_opt_ins",
        ["campaign_id"],
    )
    op.create_index(
        "ix_merchandising_campaign_opt_ins_status",
        "merchandising_campaign_opt_ins",
        ["status"],
    )
    op.create_index(
        "uq_campaign_opt_in_product",
        "merchandising_campaign_opt_ins",
        ["campaign_id", "product_id"],
        unique=True,
    )

    op.add_column(
        "promo_banners",
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_promo_banners_campaign_id",
        "promo_banners",
        "merchandising_campaigns",
        ["campaign_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_promo_banners_campaign_id", "promo_banners", ["campaign_id"])

    # Seed seasonal tags as inactive draft campaigns so admins can activate/enrich them.
    op.execute(
        """
        INSERT INTO merchandising_campaigns (
            id, slug, title, subtitle, status, is_active, is_featured, visibility,
            display_priority, product_sort, hide_out_of_stock
        )
        VALUES
            (gen_random_uuid(), 'christmas', 'Christmas Deals', 'Seasonal holiday offers', 'draft', false, false, 'public', 10, 'manual', true),
            (gen_random_uuid(), 'easter', 'Easter Offers', 'Seasonal Easter picks', 'draft', false, false, 'public', 20, 'manual', true),
            (gen_random_uuid(), 'eid', 'Eid Specials', 'Eid celebration deals', 'draft', false, false, 'public', 30, 'manual', true),
            (gen_random_uuid(), 'valentine', 'Valentine Offers', 'Gifts and romantic picks', 'draft', false, false, 'public', 40, 'manual', true),
            (gen_random_uuid(), 'black-friday', 'Black Friday Ghana', 'Biggest savings of the year', 'draft', false, false, 'public', 50, 'manual', true),
            (gen_random_uuid(), 'independence', 'Independence Day Deals', 'Celebrate Ghana Independence', 'draft', false, false, 'public', 60, 'manual', true),
            (gen_random_uuid(), 'republic-day', 'Republic Day Deals', 'Republic Day specials', 'draft', false, false, 'public', 70, 'manual', true),
            (gen_random_uuid(), 'back-to-school', 'Back To School', 'School essentials and gear', 'draft', false, false, 'public', 80, 'manual', true),
            (gen_random_uuid(), 'payday', 'Salary Week Deals', 'Payday marketplace specials', 'draft', false, false, 'public', 90, 'manual', true),
            (gen_random_uuid(), 'student', 'Student Deals', 'Campus-friendly pricing', 'draft', false, false, 'public', 100, 'manual', true),
            (gen_random_uuid(), 'free-delivery', 'Free Delivery', 'Campaigns with delivery perks', 'draft', false, false, 'public', 110, 'manual', true),
            (gen_random_uuid(), 'weekend-market', 'ODOS Weekend Market', 'Weekend marketplace highlights', 'draft', false, false, 'public', 120, 'manual', true),
            (gen_random_uuid(), 'made-in-ghana', 'Made In Ghana Deals', 'Local makers and brands', 'draft', false, false, 'public', 130, 'manual', true),
            (gen_random_uuid(), 'campus', 'Campus Deals', 'Deals for campus shoppers', 'draft', false, false, 'public', 140, 'manual', true),
            (gen_random_uuid(), 'hot-deals', 'Hot Deals Today', 'Trending discounted picks', 'draft', false, false, 'public', 150, 'manual', true)
        ON CONFLICT (slug) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_promo_banners_campaign_id", "promo_banners", type_="foreignkey")
    op.drop_index("ix_promo_banners_campaign_id", table_name="promo_banners")
    op.drop_column("promo_banners", "campaign_id")

    op.drop_index("uq_campaign_opt_in_product", table_name="merchandising_campaign_opt_ins")
    op.drop_index("ix_merchandising_campaign_opt_ins_status", table_name="merchandising_campaign_opt_ins")
    op.drop_index(
        "ix_merchandising_campaign_opt_ins_campaign_id",
        table_name="merchandising_campaign_opt_ins",
    )
    op.drop_table("merchandising_campaign_opt_ins")
    op.drop_table("merchandising_campaign_stores")
    op.drop_table("merchandising_campaign_categories")
    op.drop_table("merchandising_campaign_products")
    op.drop_index(
        "ix_merchandising_campaigns_featured_priority",
        table_name="merchandising_campaigns",
    )
    op.drop_index("ix_merchandising_campaigns_status", table_name="merchandising_campaigns")
    op.drop_index("ix_merchandising_campaigns_slug", table_name="merchandising_campaigns")
    op.drop_table("merchandising_campaigns")
