"""add vendor profiles and store assets

Revision ID: d4f0f6f2a8c1
Revises: f2c1f1bb6327
Create Date: 2026-05-05 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "d4f0f6f2a8c1"
down_revision: Union[str, None] = "f2c1f1bb6327"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


vendor_status_enum = postgresql.ENUM(
    "none",
    "pending",
    "under_review",
    "approved",
    "rejected",
    "suspended",
    name="vendor_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    vendor_status_enum.create(bind, checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "vendor_status",
            vendor_status_enum,
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "users",
        sa.Column("vendor_rejection_reason", sa.String(length=255), nullable=True),
    )

    op.create_table(
        "vendor_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            vendor_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("business_name", sa.String(length=160), nullable=False),
        sa.Column("business_category", sa.String(length=120), nullable=False),
        sa.Column("business_description", sa.String(length=1000), nullable=False),
        sa.Column("phone_number", sa.String(length=30), nullable=False),
        sa.Column("whatsapp_number", sa.String(length=30), nullable=True),
        sa.Column("region", sa.String(length=120), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("market_id", sa.String(length=50), nullable=True),
        sa.Column("store_location", sa.String(length=255), nullable=True),
        sa.Column("store_name", sa.String(length=160), nullable=False),
        sa.Column("store_description", sa.String(length=1000), nullable=True),
        sa.Column("ghana_card_number", sa.String(length=60), nullable=True),
        sa.Column(
            "business_registration_number",
            sa.String(length=120),
            nullable=True,
        ),
        sa.Column("logo_image_url", sa.String(length=500), nullable=True),
        sa.Column("banner_image_url", sa.String(length=500), nullable=True),
        sa.Column("shop_image_url", sa.String(length=500), nullable=True),
        sa.Column("rejection_reason", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_vendor_applications_user_id"),
    )
    op.create_index(
        op.f("ix_vendor_applications_user_id"),
        "vendor_applications",
        ["user_id"],
        unique=False,
    )

    op.add_column(
        "orders",
        sa.Column(
            "vendor_status",
            sa.String(length=30),
            nullable=False,
            server_default="pending",
        ),
    )
    op.create_index(op.f("ix_orders_vendor_status"), "orders", ["vendor_status"], unique=False)

    op.add_column("stores", sa.Column("market_id", sa.String(length=50), nullable=True))
    op.add_column("stores", sa.Column("image_url", sa.String(length=500), nullable=True))
    op.add_column("stores", sa.Column("region", sa.String(length=120), nullable=True))
    op.add_column(
        "stores",
        sa.Column("image_banner_url", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "stores",
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "stores",
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(op.f("ix_stores_market_id"), "stores", ["market_id"], unique=False)
    op.create_index(
        op.f("ix_stores_vendor_user_id"),
        "stores",
        ["vendor_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_stores_vendor_user_id_users",
        "stores",
        "users",
        ["vendor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("products", sa.Column("image_url", sa.String(length=500), nullable=True))
    op.add_column("products", sa.Column("description", sa.String(length=1000), nullable=True))
    op.add_column(
        "products",
        sa.Column("stock", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "products",
        sa.Column(
            "status",
            sa.String(length=30),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column("products", sa.Column("store_id", sa.String(length=50), nullable=True))
    op.add_column(
        "products",
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(op.f("ix_products_store_id"), "products", ["store_id"], unique=False)
    op.create_index(
        op.f("ix_products_vendor_user_id"),
        "products",
        ["vendor_user_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_products_store_id_stores",
        "products",
        "stores",
        ["store_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_products_vendor_user_id_users",
        "products",
        "users",
        ["vendor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_products_vendor_user_id_users", "products", type_="foreignkey")
    op.drop_constraint("fk_products_store_id_stores", "products", type_="foreignkey")
    op.drop_index(op.f("ix_products_vendor_user_id"), table_name="products")
    op.drop_index(op.f("ix_products_store_id"), table_name="products")
    op.drop_column("products", "vendor_user_id")
    op.drop_column("products", "store_id")
    op.drop_column("products", "status")
    op.drop_column("products", "stock")
    op.drop_column("products", "description")
    op.drop_column("products", "image_url")

    op.drop_constraint("fk_stores_vendor_user_id_users", "stores", type_="foreignkey")
    op.drop_index(op.f("ix_stores_vendor_user_id"), table_name="stores")
    op.drop_index(op.f("ix_stores_market_id"), table_name="stores")
    op.drop_column("stores", "vendor_user_id")
    op.drop_column("stores", "status")
    op.drop_column("stores", "image_banner_url")
    op.drop_column("stores", "region")
    op.drop_column("stores", "image_url")
    op.drop_column("stores", "market_id")

    op.drop_index(op.f("ix_orders_vendor_status"), table_name="orders")
    op.drop_column("orders", "vendor_status")

    op.drop_index(op.f("ix_vendor_applications_user_id"), table_name="vendor_applications")
    op.drop_table("vendor_applications")

    op.drop_column("users", "vendor_rejection_reason")
    op.drop_column("users", "vendor_status")

    vendor_status_enum.drop(op.get_bind(), checkfirst=True)
