"""Add per-store product sections

Revision ID: s70r3s3ct10n
Revises: r3turncust0dy

Additive only: two new tables, nothing altered on products or stores. Nothing
reads or writes them until the feature ships, so this is safe to deploy ahead
of the API.

No backfill. Guessing a shop's shelves from platform subcategories would create
sections nobody chose and leave vendors undoing them.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "s70r3s3ct10n"
down_revision = "r3turncust0dy"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def upgrade() -> None:
    tables = _tables()

    if "store_sections" not in tables:
        op.create_table(
            "store_sections",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "store_id",
                sa.String(length=50),
                sa.ForeignKey("stores.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.String(length=80), nullable=False),
            sa.Column("slug", sa.String(length=80), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
            ),
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
            sa.UniqueConstraint("store_id", "slug", name="uq_store_sections_store_slug"),
        )
        op.create_index("ix_store_sections_store_id", "store_sections", ["store_id"])

    if "store_section_products" not in tables:
        op.create_table(
            "store_section_products",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "section_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("store_sections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "product_id",
                sa.String(length=100),
                sa.ForeignKey("products.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "section_id",
                "product_id",
                name="uq_store_section_products_section_product",
            ),
        )
        op.create_index(
            "ix_store_section_products_section_id",
            "store_section_products",
            ["section_id"],
        )
        op.create_index(
            "ix_store_section_products_product_id",
            "store_section_products",
            ["product_id"],
        )


def downgrade() -> None:
    op.drop_table("store_section_products")
    op.drop_table("store_sections")
