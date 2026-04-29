"""add markets and stores tables

Revision ID: 9bc09934b1e8
Revises: 7ecf4d64e727
Create Date: 2026-04-29 10:15:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9bc09934b1e8"
down_revision: Union[str, None] = "7ecf4d64e727"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


markets_table = sa.table(
    "markets",
    sa.column("id", sa.String(length=50)),
    sa.column("slug", sa.String(length=50)),
    sa.column("title", sa.String(length=120)),
    sa.column("image_key", sa.String(length=100)),
    sa.column("sort_order", sa.Integer()),
    sa.column("is_active", sa.Boolean()),
)

stores_table = sa.table(
    "stores",
    sa.column("id", sa.String(length=50)),
    sa.column("slug", sa.String(length=80)),
    sa.column("title", sa.String(length=160)),
    sa.column("category", sa.String(length=120)),
    sa.column("market_slug", sa.String(length=50)),
    sa.column("image_key", sa.String(length=100)),
    sa.column("image_banner_key", sa.String(length=100)),
    sa.column("rating", sa.Float()),
    sa.column("address", sa.String(length=255)),
    sa.column("phone", sa.String(length=50)),
    sa.column("email", sa.String(length=255)),
    sa.column("city", sa.String(length=120)),
    sa.column("distance_km", sa.String(length=20)),
    sa.column("travel_minutes", sa.String(length=20)),
    sa.column("description", sa.String(length=255)),
    sa.column("sort_order", sa.Integer()),
    sa.column("is_active", sa.Boolean()),
)


def upgrade() -> None:
    op.create_table(
        "markets",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("slug", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("image_key", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_markets_slug"), "markets", ["slug"], unique=True)

    op.create_table(
        "stores",
        sa.Column("id", sa.String(length=50), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("market_slug", sa.String(length=50), nullable=True),
        sa.Column("image_key", sa.String(length=100), nullable=False),
        sa.Column("image_banner_key", sa.String(length=100), nullable=True),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("address", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("distance_km", sa.String(length=20), nullable=True),
        sa.Column("travel_minutes", sa.String(length=20), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_stores_market_slug"), "stores", ["market_slug"], unique=False)
    op.create_index(op.f("ix_stores_slug"), "stores", ["slug"], unique=True)

    op.bulk_insert(
        markets_table,
        [
            {"id": "mar-1", "slug": "campus", "title": "Campus", "image_key": "shoe5", "sort_order": 1, "is_active": True},
            {"id": "mar-2", "slug": "kejetia", "title": "Kejetia", "image_key": "handbag", "sort_order": 2, "is_active": True},
            {"id": "mar-3", "slug": "kanta", "title": "Kanta", "image_key": "headset", "sort_order": 3, "is_active": True},
            {"id": "mar-4", "slug": "tudu", "title": "Tudu", "image_key": "backpack1", "sort_order": 4, "is_active": True},
        ],
    )

    op.bulk_insert(
        stores_table,
        [
            {"id": "st-1", "slug": "zara-store", "title": "Zara Store", "category": "Ladies", "market_slug": "kejetia", "image_key": "backpack1", "image_banner_key": "backpack1", "rating": 4.4, "address": "Dew Street 9, Kejetia", "phone": "(+233) 54 187 4005", "email": "zara@odos.app", "city": "Kumasi", "distance_km": "9 km", "travel_minutes": "40 minutes", "description": "Fashion-forward ladies store in Kejetia.", "sort_order": 1, "is_active": True},
            {"id": "st-2", "slug": "gucci-store", "title": "Gucci Store", "category": "Ladies", "market_slug": "campus", "image_key": "dress", "image_banner_key": "ladiesstore", "rating": 4.4, "address": "Unity Hall Road, Campus", "phone": "(+233) 24 555 0002", "email": "gucci@odos.app", "city": "Kumasi", "distance_km": "6 km", "travel_minutes": "25 minutes", "description": "Premium women’s wear and accessories.", "sort_order": 2, "is_active": True},
            {"id": "st-3", "slug": "topman-store", "title": "Topman Store", "category": "Gents", "market_slug": "tudu", "image_key": "shoe5", "image_banner_key": "cloths", "rating": 4.4, "address": "Market Circle, Tudu", "phone": "(+233) 24 555 0003", "email": "topman@odos.app", "city": "Accra", "distance_km": "5 km", "travel_minutes": "18 minutes", "description": "Sharp menswear, shoes, and accessories.", "sort_order": 3, "is_active": True},
            {"id": "st-4", "slug": "deon-store", "title": "Deon Store", "category": "Groceries", "market_slug": "kejetia", "image_key": "handbag", "image_banner_key": "bag", "rating": 4.4, "address": "Central Lane, Kejetia", "phone": "(+233) 24 555 0004", "email": "deon@odos.app", "city": "Kumasi", "distance_km": "8 km", "travel_minutes": "35 minutes", "description": "Household staples and everyday groceries.", "sort_order": 4, "is_active": True},
            {"id": "st-5", "slug": "wheel-kids-store", "title": "Wheel Store", "category": "Kids", "market_slug": "campus", "image_key": "sports", "image_banner_key": "sports", "rating": 4.4, "address": "Campus Junction", "phone": "(+233) 24 555 0005", "email": "kids@odos.app", "city": "Kumasi", "distance_km": "4 km", "travel_minutes": "15 minutes", "description": "Fun kids’ fashion and school items.", "sort_order": 5, "is_active": True},
            {"id": "st-6", "slug": "wheel-auto-store", "title": "Wheel Store", "category": "Automobile", "market_slug": "kanta", "image_key": "automotive", "image_banner_key": "automotive", "rating": 4.4, "address": "Auto Parts Row, Kanta", "phone": "(+233) 24 555 0006", "email": "auto@odos.app", "city": "Accra", "distance_km": "12 km", "travel_minutes": "50 minutes", "description": "Automotive tools and accessories.", "sort_order": 6, "is_active": True},
            {"id": "st-7", "slug": "wheel-beauty-store", "title": "Wheel Store", "category": "Beauty", "market_slug": "kejetia", "image_key": "cosmetics", "image_banner_key": "cosmetics", "rating": 4.4, "address": "Beauty Block, Kejetia", "phone": "(+233) 24 555 0007", "email": "beauty@odos.app", "city": "Kumasi", "distance_km": "10 km", "travel_minutes": "42 minutes", "description": "Cosmetics, skincare, and beauty essentials.", "sort_order": 7, "is_active": True},
            {"id": "st-8", "slug": "wheel-others-store", "title": "Wheel Store", "category": "Others", "market_slug": "campus", "image_key": "headset", "image_banner_key": "headset", "rating": 4.4, "address": "Campus Annex", "phone": "(+233) 24 555 0008", "email": "others@odos.app", "city": "Kumasi", "distance_km": "7 km", "travel_minutes": "29 minutes", "description": "Mixed lifestyle finds and accessories.", "sort_order": 8, "is_active": True},
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_stores_slug"), table_name="stores")
    op.drop_index(op.f("ix_stores_market_slug"), table_name="stores")
    op.drop_table("stores")
    op.drop_index(op.f("ix_markets_slug"), table_name="markets")
    op.drop_table("markets")
