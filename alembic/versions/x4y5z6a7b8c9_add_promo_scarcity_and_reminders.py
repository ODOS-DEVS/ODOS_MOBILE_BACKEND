"""Add flash-sale scarcity tracking (stock_limit/units_sold on flash sale
event products, flash attribution on order items) and expiry-reminder dedupe
columns for vouchers/voucher assignments."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "x4y5z6a7b8c9"
down_revision = "w3x4y5z6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    flash_product_columns = {
        col["name"] for col in inspector.get_columns("flash_sale_event_products")
    }
    if "stock_limit" not in flash_product_columns:
        op.add_column(
            "flash_sale_event_products",
            sa.Column("stock_limit", sa.Integer(), nullable=True),
        )
    if "units_sold" not in flash_product_columns:
        op.add_column(
            "flash_sale_event_products",
            sa.Column(
                "units_sold",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )

    order_item_columns = {col["name"] for col in inspector.get_columns("order_items")}
    if "is_flash_sale" not in order_item_columns:
        op.add_column(
            "order_items",
            sa.Column(
                "is_flash_sale",
                sa.Boolean(),
                nullable=False,
                server_default="false",
            ),
        )
    if "flash_sale_event_id" not in order_item_columns:
        op.add_column(
            "order_items",
            sa.Column(
                "flash_sale_event_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
        )
        op.create_foreign_key(
            "fk_order_items_flash_sale_event_id",
            "order_items",
            "flash_sale_events",
            ["flash_sale_event_id"],
            ["id"],
            ondelete="SET NULL",
        )

    voucher_assignment_columns = {
        col["name"] for col in inspector.get_columns("voucher_assignments")
    }
    if "expiry_reminder_sent_at" not in voucher_assignment_columns:
        op.add_column(
            "voucher_assignments",
            sa.Column("expiry_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        )

    voucher_columns = {col["name"] for col in inspector.get_columns("vouchers")}
    if "vendor_expiry_reminder_sent_at" not in voucher_columns:
        op.add_column(
            "vouchers",
            sa.Column(
                "vendor_expiry_reminder_sent_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    voucher_columns = {col["name"] for col in inspector.get_columns("vouchers")}
    if "vendor_expiry_reminder_sent_at" in voucher_columns:
        op.drop_column("vouchers", "vendor_expiry_reminder_sent_at")

    voucher_assignment_columns = {
        col["name"] for col in inspector.get_columns("voucher_assignments")
    }
    if "expiry_reminder_sent_at" in voucher_assignment_columns:
        op.drop_column("voucher_assignments", "expiry_reminder_sent_at")

    order_item_columns = {col["name"] for col in inspector.get_columns("order_items")}
    if "flash_sale_event_id" in order_item_columns:
        op.drop_constraint(
            "fk_order_items_flash_sale_event_id",
            "order_items",
            type_="foreignkey",
        )
        op.drop_column("order_items", "flash_sale_event_id")
    if "is_flash_sale" in order_item_columns:
        op.drop_column("order_items", "is_flash_sale")

    flash_product_columns = {
        col["name"] for col in inspector.get_columns("flash_sale_event_products")
    }
    if "units_sold" in flash_product_columns:
        op.drop_column("flash_sale_event_products", "units_sold")
    if "stock_limit" in flash_product_columns:
        op.drop_column("flash_sale_event_products", "stock_limit")
