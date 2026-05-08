"""add category media and product taxonomy

Revision ID: c4d8e1b7a2f0
Revises: a91b7d22f4a8
Create Date: 2026-05-07 09:30:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.core.catalog_taxonomy import ODOS_CATEGORY_TAXONOMY


# revision identifiers, used by Alembic.
revision = "c4d8e1b7a2f0"
down_revision = "a91b7d22f4a8"
branch_labels = None
depends_on = None


category_table = sa.table(
    "categories",
    sa.column("id", sa.String(length=50)),
    sa.column("slug", sa.String(length=50)),
    sa.column("title", sa.String(length=120)),
    sa.column("subtitle", sa.String(length=160)),
    sa.column("image_key", sa.String(length=100)),
    sa.column("image_url", sa.String(length=500)),
    sa.column("subcategories", postgresql.ARRAY(sa.String(length=120))),
    sa.column("sort_order", sa.Integer()),
    sa.column("is_active", sa.Boolean()),
)


def upgrade() -> None:
    op.add_column("categories", sa.Column("image_url", sa.String(length=500), nullable=True))
    op.add_column(
        "categories",
        sa.Column(
            "subcategories",
            postgresql.ARRAY(sa.String(length=120)),
            nullable=True,
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "category_slugs",
            postgresql.ARRAY(sa.String(length=80)),
            nullable=True,
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "subcategory_slugs",
            postgresql.ARRAY(sa.String(length=120)),
            nullable=True,
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE products
            SET category_slugs = CASE
                WHEN category IS NULL OR btrim(category) = '' THEN NULL
                ELSE ARRAY[
                    regexp_replace(lower(btrim(category)), '[^a-z0-9]+', '-', 'g')
                ]
            END,
            subcategory_slugs = CASE
                WHEN subcategory IS NULL OR btrim(subcategory) = '' THEN NULL
                ELSE ARRAY[
                    regexp_replace(lower(btrim(subcategory)), '[^a-z0-9]+', '-', 'g')
                ]
            END
            """
        )
    )

    bind = op.get_bind()

    for index, entry in enumerate(ODOS_CATEGORY_TAXONOMY, start=1):
        existing = bind.execute(
            sa.text("SELECT id FROM categories WHERE slug = :slug"),
            {"slug": entry["slug"]},
        ).scalar_one_or_none()

        values = {
            "slug": entry["slug"],
            "title": entry["title"],
            "subtitle": entry["subtitle"],
            "image_key": entry["image_key"],
            "image_url": None,
            "subcategories": entry["subcategories"],
            "sort_order": index,
            "is_active": True,
        }

        if existing:
            bind.execute(
                category_table.update()
                .where(category_table.c.id == existing)
                .values(**values),
            )
            continue

        bind.execute(
            category_table.insert().values(
                id=f"category-{entry['slug']}",
                **values,
            )
        )


def downgrade() -> None:
    op.drop_column("products", "subcategory_slugs")
    op.drop_column("products", "category_slugs")
    op.drop_column("categories", "subcategories")
    op.drop_column("categories", "image_url")
