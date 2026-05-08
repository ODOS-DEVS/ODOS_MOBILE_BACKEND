"""add catalog merchandising fields

Revision ID: e7a2b4c9d1f0
Revises: d4f0f6f2a8c1
Create Date: 2026-05-05 20:10:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "e7a2b4c9d1f0"
down_revision = "d4f0f6f2a8c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("subcategory", sa.String(length=120), nullable=True))
    op.add_column(
        "products",
        sa.Column("image_urls", postgresql.ARRAY(sa.String(length=500)), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("color_options", postgresql.ARRAY(sa.String(length=80)), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("size_options", postgresql.ARRAY(sa.String(length=40)), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("placement_tags", postgresql.ARRAY(sa.String(length=50)), nullable=True),
    )
    op.add_column(
        "stores",
        sa.Column("audience_slugs", postgresql.ARRAY(sa.String(length=50)), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stores", "audience_slugs")
    op.drop_column("products", "placement_tags")
    op.drop_column("products", "size_options")
    op.drop_column("products", "color_options")
    op.drop_column("products", "image_urls")
    op.drop_column("products", "subcategory")
