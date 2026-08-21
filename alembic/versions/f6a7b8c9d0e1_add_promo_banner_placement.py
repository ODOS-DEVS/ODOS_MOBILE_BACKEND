"""add promo banner placement

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-19

"""

from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "promo_banners",
        sa.Column(
            "placement",
            sa.String(length=30),
            server_default="home",
            nullable=False,
        ),
    )
    op.create_index("ix_promo_banners_placement", "promo_banners", ["placement"])
    op.execute(
        """
        UPDATE promo_banners
        SET link_type = CASE
            WHEN cta_link ILIKE '%flash%' THEN 'flash_sales'
            WHEN cta_link ILIKE '%popular%' THEN 'popular'
            WHEN cta_link ILIKE '%voucher%' THEN 'vouchers'
            WHEN cta_link ILIKE '%deals%' THEN 'deals'
            WHEN cta_link ILIKE 'http%' THEN 'external'
            ELSE link_type
        END
        WHERE cta_link IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_promo_banners_placement", table_name="promo_banners")
    op.drop_column("promo_banners", "placement")
