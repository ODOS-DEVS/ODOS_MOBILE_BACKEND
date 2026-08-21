"""add delivery settings

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-06-24

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "h8i9j0k1l2m3"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None

DEFAULT_REGIONS = [
    "greater accra",
    "accra",
    "tema",
    "madina",
    "ashaiman",
    "ga east",
    "ga west",
    "ga central",
    "ga south",
    "la dade kotopon",
    "ledzokuku",
    "krowor",
    "adenta",
    "ablekuma",
]


def upgrade() -> None:
    op.create_table(
        "delivery_settings",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("free_shipping_threshold", sa.Float(), nullable=False, server_default="299"),
        sa.Column("economy_fee", sa.Float(), nullable=False, server_default="19"),
        sa.Column("express_fee", sa.Float(), nullable=False, server_default="29"),
        sa.Column("same_day_fee", sa.Float(), nullable=False, server_default="49"),
        sa.Column("same_day_cutoff_hour", sa.Integer(), nullable=False, server_default="14"),
        sa.Column("same_day_regions", postgresql.ARRAY(sa.String(length=80)), nullable=False),
        sa.Column("economy_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("express_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("same_day_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("economy_title", sa.String(length=80), nullable=False, server_default="Standard delivery"),
        sa.Column("economy_eta", sa.String(length=80), nullable=False, server_default="3–5 business days"),
        sa.Column("express_title", sa.String(length=80), nullable=False, server_default="Express delivery"),
        sa.Column("express_eta", sa.String(length=80), nullable=False, server_default="1–2 business days"),
        sa.Column("same_day_title", sa.String(length=80), nullable=False, server_default="Same-day delivery"),
        sa.Column("same_day_eta", sa.String(length=80), nullable=False, server_default="Today"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    settings_table = sa.table(
        "delivery_settings",
        sa.column("id", sa.String),
        sa.column("free_shipping_threshold", sa.Float),
        sa.column("economy_fee", sa.Float),
        sa.column("express_fee", sa.Float),
        sa.column("same_day_fee", sa.Float),
        sa.column("same_day_cutoff_hour", sa.Integer),
        sa.column("same_day_regions", postgresql.ARRAY(sa.String)),
    )
    op.bulk_insert(
        settings_table,
        [
            {
                "id": "default",
                "free_shipping_threshold": 299.0,
                "economy_fee": 19.0,
                "express_fee": 29.0,
                "same_day_fee": 49.0,
                "same_day_cutoff_hour": 14,
                "same_day_regions": DEFAULT_REGIONS,
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("delivery_settings")
