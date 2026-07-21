"""Add Seller Center Wave 2 fields: inventory ledger and vendor notify prefs.

Revision ID: t0u1v2w3x4y5
Revises: s9t0u1v2w3x4
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "t0u1v2w3x4y5"
down_revision = "s9t0u1v2w3x4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("store_id", sa.String(length=50), nullable=True),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("stock_after", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("reference_type", sa.String(length=50), nullable=True),
        sa.Column("reference_id", sa.String(length=100), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vendor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["store_id"], ["stores.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_inventory_movements_product_id"),
        "inventory_movements",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_vendor_user_id"),
        "inventory_movements",
        ["vendor_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_store_id"),
        "inventory_movements",
        ["store_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_reason"),
        "inventory_movements",
        ["reason"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_reference_id"),
        "inventory_movements",
        ["reference_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_inventory_movements_created_at"),
        "inventory_movements",
        ["created_at"],
        unique=False,
    )

    op.add_column(
        "users",
        sa.Column("vendor_notify_orders", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "users",
        sa.Column("vendor_notify_inventory", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "users",
        sa.Column("vendor_notify_reviews", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "users",
        sa.Column("vendor_notify_payouts", sa.Boolean(), nullable=False, server_default="true"),
    )
    # Preserve any existing per-user opt-out when introducing the new source-of-truth column.
    op.execute("UPDATE users SET vendor_notify_orders = vendor_order_notifications")


def downgrade() -> None:
    op.drop_column("users", "vendor_notify_payouts")
    op.drop_column("users", "vendor_notify_reviews")
    op.drop_column("users", "vendor_notify_inventory")
    op.drop_column("users", "vendor_notify_orders")

    op.drop_index(op.f("ix_inventory_movements_created_at"), table_name="inventory_movements")
    op.drop_index(op.f("ix_inventory_movements_reference_id"), table_name="inventory_movements")
    op.drop_index(op.f("ix_inventory_movements_reason"), table_name="inventory_movements")
    op.drop_index(op.f("ix_inventory_movements_store_id"), table_name="inventory_movements")
    op.drop_index(op.f("ix_inventory_movements_vendor_user_id"), table_name="inventory_movements")
    op.drop_index(op.f("ix_inventory_movements_product_id"), table_name="inventory_movements")
    op.drop_table("inventory_movements")
