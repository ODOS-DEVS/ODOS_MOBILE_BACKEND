"""add orders tables

Revision ID: a51d7299e7f4
Revises: 9bc09934b1e8
Create Date: 2026-04-29 16:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a51d7299e7f4"
down_revision: Union[str, None] = "9bc09934b1e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_number", sa.String(length=30), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="buy_now"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="processing"),
        sa.Column("subtotal_amount", sa.Float(), nullable=False),
        sa.Column("shipping_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Float(), nullable=False),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("tracking_eta", sa.String(length=120), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=255), nullable=True),
        sa.Column("address_full_name", sa.String(length=120), nullable=False),
        sa.Column("address_phone", sa.String(length=30), nullable=False),
        sa.Column("address_street", sa.String(length=255), nullable=False),
        sa.Column("address_city", sa.String(length=120), nullable=False),
        sa.Column("address_region", sa.String(length=120), nullable=False),
        sa.Column("payment_type", sa.String(length=30), nullable=False),
        sa.Column("payment_label", sa.String(length=120), nullable=False),
        sa.Column("payment_network", sa.String(length=60), nullable=True),
        sa.Column("payment_phone", sa.String(length=30), nullable=True),
        sa.Column("payment_last4", sa.String(length=4), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_orders_order_number"), "orders", ["order_number"], unique=True)
    op.create_index(op.f("ix_orders_status"), "orders", ["status"], unique=False)
    op.create_index(op.f("ix_orders_user_id"), "orders", ["user_id"], unique=False)

    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("image_key", sa.String(length=100), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Float(), nullable=False),
        sa.Column("line_total", sa.Float(), nullable=False),
        sa.Column("selected_color", sa.String(length=60), nullable=True),
        sa.Column("selected_size", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_order_items_order_id"), "order_items", ["order_id"], unique=False)
    op.create_index(op.f("ix_order_items_product_id"), "order_items", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_order_items_product_id"), table_name="order_items")
    op.drop_index(op.f("ix_order_items_order_id"), table_name="order_items")
    op.drop_table("order_items")
    op.drop_index(op.f("ix_orders_user_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_status"), table_name="orders")
    op.drop_index(op.f("ix_orders_order_number"), table_name="orders")
    op.drop_table("orders")
