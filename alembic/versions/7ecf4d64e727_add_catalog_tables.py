"""add catalog tables

Revision ID: 7ecf4d64e727
Revises: 205db7c9f562
Create Date: 2026-04-28 18:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7ecf4d64e727"
down_revision: Union[str, None] = "205db7c9f562"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


categories_table = sa.table(
    "categories",
    sa.column("id", sa.String(length=50)),
    sa.column("slug", sa.String(length=50)),
    sa.column("title", sa.String(length=120)),
    sa.column("subtitle", sa.String(length=160)),
    sa.column("image_key", sa.String(length=100)),
    sa.column("sort_order", sa.Integer()),
    sa.column("is_active", sa.Boolean()),
)

products_table = sa.table(
    "products",
    sa.column("id", sa.String(length=100)),
    sa.column("audience_slug", sa.String(length=50)),
    sa.column("section", sa.String(length=50)),
    sa.column("title", sa.String(length=255)),
    sa.column("category", sa.String(length=120)),
    sa.column("price", sa.Integer()),
    sa.column("old_price", sa.Integer()),
    sa.column("discount", sa.String(length=50)),
    sa.column("rating", sa.Float()),
    sa.column("reviews", sa.String(length=50)),
    sa.column("image_key", sa.String(length=100)),
    sa.column("sort_order", sa.Integer()),
    sa.column("is_active", sa.Boolean()),
)


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("slug", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("subtitle", sa.String(length=160), nullable=False),
        sa.Column("image_key", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_categories_slug"), "categories", ["slug"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", sa.String(length=100), nullable=False),
        sa.Column("audience_slug", sa.String(length=50), nullable=True),
        sa.Column("section", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("old_price", sa.Integer(), nullable=True),
        sa.Column("discount", sa.String(length=50), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("reviews", sa.String(length=50), nullable=True),
        sa.Column("image_key", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_products_audience_slug"), "products", ["audience_slug"], unique=False)
    op.create_index(op.f("ix_products_section"), "products", ["section"], unique=False)

    op.bulk_insert(
        categories_table,
        [
            {"id": "1", "slug": "gents", "title": "Gents", "subtitle": "All about men", "image_key": "gents", "sort_order": 1, "is_active": True},
            {"id": "2", "slug": "ladies", "title": "Ladies", "subtitle": "Ladies dorm", "image_key": "ladiesstore", "sort_order": 2, "is_active": True},
            {"id": "3", "slug": "kids", "title": "Kids", "subtitle": "Kids zone", "image_key": "sports", "sort_order": 3, "is_active": True},
            {"id": "4", "slug": "sports", "title": "Sports", "subtitle": "Sports land", "image_key": "sports", "sort_order": 4, "is_active": True},
            {"id": "5", "slug": "electronics", "title": "Electronics", "subtitle": "All electronics", "image_key": "headset", "sort_order": 5, "is_active": True},
            {"id": "6", "slug": "beauty", "title": "Beauty", "subtitle": "Glow world", "image_key": "cosmetics", "sort_order": 6, "is_active": True},
            {"id": "7", "slug": "automobile", "title": "Automobile", "subtitle": "Vehicles land", "image_key": "automotive", "sort_order": 7, "is_active": True},
        ],
    )

    op.bulk_insert(
        products_table,
        [
            {"id": "gents-1", "audience_slug": "gents", "section": None, "title": "Classic Leather Wallet", "category": "Accessories", "price": 120, "old_price": 150, "discount": None, "rating": 4.8, "reviews": "1.2k", "image_key": "bag", "sort_order": 1, "is_active": True},
            {"id": "gents-2", "audience_slug": "gents", "section": None, "title": "Men’s Analog Watch", "category": "Watches", "price": 250, "old_price": 300, "discount": "20% off", "rating": 4.6, "reviews": "980", "image_key": "headset", "sort_order": 2, "is_active": True},
            {"id": "gents-3", "audience_slug": "gents", "section": None, "title": "Brown Leather Belt", "category": "Accessories", "price": 90, "old_price": 110, "discount": None, "rating": 4.4, "reviews": "540", "image_key": "headset", "sort_order": 3, "is_active": True},
            {"id": "gents-4", "audience_slug": "gents", "section": None, "title": "Formal Loafers", "category": "Shoes", "price": 350, "old_price": 400, "discount": "15% off", "rating": 4.7, "reviews": "760", "image_key": "shoe5", "sort_order": 4, "is_active": True},
            {"id": "gents-5", "audience_slug": "gents", "section": None, "title": "Men’s Sunglasses", "category": "Eyewear", "price": 180, "old_price": 200, "discount": None, "rating": 4.5, "reviews": "1.1k", "image_key": "shoe5", "sort_order": 5, "is_active": True},
            {"id": "gents-6", "audience_slug": "gents", "section": None, "title": "Casual Denim Jacket", "category": "Clothing", "price": 320, "old_price": 360, "discount": None, "rating": 4.9, "reviews": "1.5k", "image_key": "bag", "sort_order": 6, "is_active": True},
            {"id": "gents-7", "audience_slug": "gents", "section": None, "title": "Men’s Backpack", "category": "Bags", "price": 280, "old_price": 320, "discount": "10% off", "rating": 4.3, "reviews": "640", "image_key": "backpack1", "sort_order": 7, "is_active": True},
            {"id": "gents-8", "audience_slug": "gents", "section": None, "title": "Running Sneakers", "category": "Shoes", "price": 400, "old_price": 450, "discount": None, "rating": 4.7, "reviews": "880", "image_key": "shoe5", "sort_order": 8, "is_active": True},
            {"id": "ladies-1", "audience_slug": "ladies", "section": None, "title": "Elegant Handbag Clutch", "category": "Bags", "price": 150, "old_price": 190, "discount": None, "rating": 4.7, "reviews": "820", "image_key": "handbag", "sort_order": 1, "is_active": True},
            {"id": "ladies-2", "audience_slug": "ladies", "section": None, "title": "Women’s Designer Watch", "category": "Watches", "price": 320, "old_price": 400, "discount": "20% off", "rating": 4.9, "reviews": "650", "image_key": "headset", "sort_order": 2, "is_active": True},
            {"id": "ladies-3", "audience_slug": "ladies", "section": None, "title": "Gold Hoop Earrings Set", "category": "Jewellery", "price": 75, "old_price": 95, "discount": None, "rating": 4.4, "reviews": "430", "image_key": "handbag", "sort_order": 3, "is_active": True},
            {"id": "ladies-4", "audience_slug": "ladies", "section": None, "title": "Formal Stiletto Heels", "category": "Shoes", "price": 220, "old_price": 270, "discount": "18% off", "rating": 4.8, "reviews": "570", "image_key": "shoe5", "sort_order": 4, "is_active": True},
            {"id": "ladies-5", "audience_slug": "ladies", "section": None, "title": "Silk Summer Dress", "category": "Clothing", "price": 180, "old_price": 230, "discount": None, "rating": 4.6, "reviews": "910", "image_key": "dress", "sort_order": 5, "is_active": True},
            {"id": "ladies-6", "audience_slug": "ladies", "section": None, "title": "Makeup & Beauty Kit", "category": "Beauty", "price": 130, "old_price": 155, "discount": None, "rating": 4.5, "reviews": "680", "image_key": "dress", "sort_order": 6, "is_active": True},
            {"id": "ladies-7", "audience_slug": "ladies", "section": None, "title": "Women’s Tote Bag", "category": "Bags", "price": 140, "old_price": 170, "discount": "12% off", "rating": 4.3, "reviews": "490", "image_key": "handbag", "sort_order": 7, "is_active": True},
            {"id": "ladies-8", "audience_slug": "ladies", "section": None, "title": "Casual Sneakers for Women", "category": "Shoes", "price": 195, "old_price": 240, "discount": None, "rating": 4.7, "reviews": "730", "image_key": "shoe5", "sort_order": 8, "is_active": True},
            {"id": "kids-1", "audience_slug": "kids", "section": None, "title": "Colorful Kids Backpack", "category": "Bags", "price": 80, "old_price": 100, "discount": None, "rating": 4.6, "reviews": "430", "image_key": "handbag", "sort_order": 1, "is_active": True},
            {"id": "kids-2", "audience_slug": "kids", "section": None, "title": "Boys’ Sneakers High Top", "category": "Shoes", "price": 95, "old_price": 120, "discount": "20% off", "rating": 4.7, "reviews": "540", "image_key": "shoe5", "sort_order": 2, "is_active": True},
            {"id": "kids-3", "audience_slug": "kids", "section": None, "title": "Kids’ Hoodie Sweatshirt", "category": "Clothing", "price": 60, "old_price": 75, "discount": None, "rating": 4.4, "reviews": "310", "image_key": "dress", "sort_order": 3, "is_active": True},
            {"id": "kids-4", "audience_slug": "kids", "section": None, "title": "Girls’ Mermaid Dress", "category": "Clothing", "price": 70, "old_price": 90, "discount": "22% off", "rating": 4.8, "reviews": "390", "image_key": "bag", "sort_order": 4, "is_active": True},
            {"id": "kids-5", "audience_slug": "kids", "section": None, "title": "Kids’ Sunglasses Set", "category": "Eyewear", "price": 40, "old_price": 50, "discount": None, "rating": 4.5, "reviews": "260", "image_key": "backpack1", "sort_order": 5, "is_active": True},
            {"id": "kids-6", "audience_slug": "kids", "section": None, "title": "Kids’ Smart Watch", "category": "Watches", "price": 110, "old_price": 140, "discount": None, "rating": 4.3, "reviews": "320", "image_key": "headset", "sort_order": 6, "is_active": True},
            {"id": "kids-7", "audience_slug": "kids", "section": None, "title": "Kids’ Winter Boots", "category": "Shoes", "price": 85, "old_price": 105, "discount": "15% off", "rating": 4.6, "reviews": "370", "image_key": "shoe5", "sort_order": 7, "is_active": True},
            {"id": "kids-8", "audience_slug": "kids", "section": None, "title": "Kids’ Puzzle & Game Set", "category": "Toys", "price": 45, "old_price": 60, "discount": None, "rating": 4.7, "reviews": "430", "image_key": "bag", "sort_order": 8, "is_active": True},
            {"id": "flash-1", "audience_slug": None, "section": "flash_sales", "title": "Karia blo ping backpack", "category": "Backpack travel", "price": 59, "old_price": 69, "discount": None, "rating": 4.4, "reviews": "320k", "image_key": "headset", "sort_order": 1, "is_active": True},
            {"id": "flash-2", "audience_slug": None, "section": "flash_sales", "title": "Erliana blo ping backpack", "category": "Backpack travel", "price": 55, "old_price": 69, "discount": "50% off", "rating": 4.3, "reviews": "320k", "image_key": "bag", "sort_order": 2, "is_active": True},
            {"id": "flash-3", "audience_slug": None, "section": "flash_sales", "title": "Erliana blo ping backpack", "category": "Backpack travel", "price": 55, "old_price": 69, "discount": "50% off", "rating": 4.3, "reviews": "320k", "image_key": "handbag", "sort_order": 3, "is_active": True},
            {"id": "flash-4", "audience_slug": None, "section": "flash_sales", "title": "Erliana blo ping backpack", "category": "Backpack travel", "price": 55, "old_price": 69, "discount": "50% off", "rating": 4.3, "reviews": "320k", "image_key": "shoe5", "sort_order": 4, "is_active": True},
            {"id": "flash-5", "audience_slug": None, "section": "flash_sales", "title": "Erliana blo ping backpack", "category": "Backpack travel", "price": 55, "old_price": 69, "discount": "50% off", "rating": 4.3, "reviews": "320k", "image_key": "dress", "sort_order": 5, "is_active": True},
            {"id": "rec-1", "audience_slug": None, "section": "recommendations", "title": "Karia backpack", "category": "Backpack, travel", "price": 59, "old_price": None, "discount": None, "rating": 4.4, "reviews": "120", "image_key": "backpack1", "sort_order": 1, "is_active": True},
            {"id": "rec-2", "audience_slug": None, "section": "recommendations", "title": "Eliana backpack", "category": "Backpack, travel", "price": 70, "old_price": None, "discount": None, "rating": 4.6, "reviews": "90", "image_key": "backpack1", "sort_order": 2, "is_active": True},
            {"id": "rec-3", "audience_slug": None, "section": "recommendations", "title": "Geria backpack", "category": "Backpack, travel", "price": 77, "old_price": None, "discount": None, "rating": 4.2, "reviews": "102", "image_key": "backpack1", "sort_order": 3, "is_active": True},
            {"id": "pop-1", "audience_slug": None, "section": "popular", "title": "Karia blo ping backpack", "category": "Backpack travel", "price": 59, "old_price": None, "discount": None, "rating": 4.4, "reviews": "320k", "image_key": "handbag", "sort_order": 1, "is_active": True},
            {"id": "pop-2", "audience_slug": None, "section": "popular", "title": "Zara", "category": "Slimfit shirt", "price": 59, "old_price": 69, "discount": None, "rating": 4.4, "reviews": "320k", "image_key": "shoe5", "sort_order": 2, "is_active": True},
            {"id": "pop-3", "audience_slug": None, "section": "popular", "title": "Zara", "category": "Slimfit shirt", "price": 59, "old_price": 69, "discount": None, "rating": 4.4, "reviews": None, "image_key": "bag", "sort_order": 3, "is_active": True},
            {"id": "pop-4", "audience_slug": None, "section": "popular", "title": "Zara", "category": "Slimfit shirt", "price": 59, "old_price": None, "discount": None, "rating": 4.4, "reviews": None, "image_key": "dress", "sort_order": 4, "is_active": True},
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_products_section"), table_name="products")
    op.drop_index(op.f("ix_products_audience_slug"), table_name="products")
    op.drop_table("products")
    op.drop_index(op.f("ix_categories_slug"), table_name="categories")
    op.drop_table("categories")
