"""Split an order into one package per vendor, and let shops price delivery

Revision ID: pkg1sp1itd3l
Revises: d3l1v3ry0n3

Three things land together because they are one decision:

1. `order_packages` -- one row per (order, vendor). A multi-vendor order used
   to share a single `vendor_status` / `delivery_status` pair on `orders`,
   which meant the second vendor to touch an order was locked out by the
   forward-transition guard, the first vendor to dispatch told the customer the
   *whole* order was on its way, and confirming that one package paid every
   vendor on the order. Each package now owns its own stage, its own clock and
   its own settlement.

2. Per-store delivery pricing on `stores`. All nullable, all meaning "use the
   platform default" -- a store that never opens the setting prices exactly as
   it does today.

3. `vendor_wallet_transactions.delivery_fee_amount`, so a settlement can show
   the vendor what they were paid for the ride separately from the goods.

Backfill: every existing order gets its packages, derived from the distinct
`vendor_user_id` values on its items, copying the order's current status and
timestamps onto each so nothing in flight changes stage.

`delivery_fee` is deliberately backfilled to **0** on every historical package.
Those orders were priced, charged and (mostly) settled under the old rule where
ODOS kept the shipping fee; writing a non-zero fee here would claim a vendor is
owed money for a delivery that was already paid out the old way. Orders still
in flight therefore settle exactly as they would have. The new rule applies
from the first order placed after this migration.

Items are not repointed at packages -- ownership is resolved by
`order_items.vendor_user_id`, which is already the codebase's single answer to
"whose item is this".
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "pkg1sp1itd3l"
down_revision = "d3l1v3ry0n3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- 1. Per-store delivery pricing -----------------------------------
    op.add_column("stores", sa.Column("delivery_fee_economy", sa.Float(), nullable=True))
    op.add_column("stores", sa.Column("delivery_fee_express", sa.Float(), nullable=True))
    op.add_column("stores", sa.Column("delivery_fee_same_day", sa.Float(), nullable=True))
    op.add_column("stores", sa.Column("free_delivery_threshold", sa.Float(), nullable=True))
    op.add_column(
        "stores",
        sa.Column(
            "express_delivery_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "stores",
        sa.Column(
            "same_day_delivery_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )

    # --- 2. Delivery fee visible on the vendor's wallet ------------------
    op.add_column(
        "vendor_wallet_transactions",
        sa.Column("delivery_fee_amount", sa.Float(), nullable=True),
    )

    # --- 3. The packages themselves --------------------------------------
    op.create_table(
        "order_packages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vendor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "store_id",
            sa.String(length=50),
            sa.ForeignKey("stores.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("store_name", sa.String(length=160), nullable=True),
        sa.Column("package_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("vendor_status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column(
            "delivery_status", sa.String(length=30), nullable=False, server_default="not_dispatched"
        ),
        sa.Column("items_subtotal", sa.Float(), nullable=False, server_default="0"),
        sa.Column("discount_share", sa.Float(), nullable=False, server_default="0"),
        sa.Column("delivery_fee", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "delivery_fee_waived", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "settlement_status", sa.String(length=20), nullable=False, server_default="not_eligible"
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=255), nullable=True),
        sa.Column("confirmation_method", sa.String(length=20), nullable=True),
        sa.Column("auto_release_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_problem_reason", sa.String(length=500), nullable=True),
        sa.Column("delivery_problem_reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reschedule_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reschedule_note", sa.String(length=280), nullable=True),
        sa.Column("dispatch_photo_url", sa.String(length=500), nullable=True),
        sa.Column("dispatch_photo_key", sa.String(length=100), nullable=True),
        sa.Column("departure_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tracking_eta", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.UniqueConstraint("order_id", "vendor_user_id", name="uq_order_package_order_vendor"),
    )
    op.create_index("ix_order_packages_order_id", "order_packages", ["order_id"])
    op.create_index("ix_order_packages_vendor_user_id", "order_packages", ["vendor_user_id"])
    op.create_index("ix_order_packages_store_id", "order_packages", ["store_id"])
    op.create_index("ix_order_packages_vendor_status", "order_packages", ["vendor_status"])
    op.create_index("ix_order_packages_delivery_status", "order_packages", ["delivery_status"])
    op.create_index("ix_order_packages_settlement_status", "order_packages", ["settlement_status"])
    # The auto-release sweep's only query: "packages out for delivery whose
    # grace window has elapsed". Partial so it stays small as history grows.
    op.create_index(
        "ix_order_packages_auto_release_due",
        "order_packages",
        ["auto_release_at"],
        postgresql_where=sa.text("delivery_status = 'out_for_delivery'"),
    )

    # --- 4. Backfill every existing order --------------------------------
    # One package per distinct vendor on the order's items, numbered by the
    # order the vendor's first item was added, carrying the order's current
    # state so nothing in flight moves stage. Items with no vendor_user_id are
    # excluded for the same reason settlement already excludes them: there is
    # no vendor to fulfil or pay.
    op.execute(
        """
        INSERT INTO order_packages (
            id, order_id, vendor_user_id, store_id, store_name, package_number,
            vendor_status, delivery_status, items_subtotal, discount_share,
            delivery_fee, delivery_fee_waived, settlement_status,
            dispatched_at, dispatch_attempt_count, delivered_at, cancelled_at,
            cancellation_reason, confirmation_method, auto_release_at,
            auto_released_at, delivery_reminder_sent_at,
            delivery_problem_reason, delivery_problem_reported_at,
            reschedule_requested_at, reschedule_note, dispatch_photo_url,
            dispatch_photo_key, departure_notified_at, tracking_eta,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(),
            grouped.order_id,
            grouped.vendor_user_id,
            grouped.store_id,
            s.title,
            ROW_NUMBER() OVER (
                PARTITION BY grouped.order_id ORDER BY grouped.first_item_at, grouped.vendor_user_id
            ),
            o.vendor_status,
            o.delivery_status,
            grouped.items_subtotal,
            -- Historical discount split proportionally by this vendor's share
            -- of the order subtotal. Matches how vendor_allocation_map has
            -- always divided it, so settled orders reconcile.
            CASE
                WHEN COALESCE(o.subtotal_amount, 0) > 0
                THEN ROUND((COALESCE(o.discount_amount, 0) * grouped.items_subtotal
                            / o.subtotal_amount)::numeric, 2)
                ELSE 0
            END,
            0,      -- delivery_fee: see the module docstring
            FALSE,
            o.settlement_status,
            o.dispatched_at,
            o.dispatch_attempt_count,
            o.delivered_at,
            o.cancelled_at,
            o.cancellation_reason,
            o.confirmation_method,
            o.auto_release_at,
            o.auto_released_at,
            o.delivery_reminder_sent_at,
            o.delivery_problem_reason,
            o.delivery_problem_reported_at,
            o.reschedule_requested_at,
            o.reschedule_note,
            o.dispatch_photo_url,
            o.dispatch_photo_key,
            o.departure_notified_at,
            o.tracking_eta,
            COALESCE(o.placed_at, o.created_at, now()),
            now()
        FROM (
            SELECT
                oi.order_id,
                oi.vendor_user_id,
                MIN(oi.store_id)          AS store_id,
                MIN(oi.created_at)        AS first_item_at,
                ROUND(SUM(oi.line_total)::numeric, 2) AS items_subtotal
            FROM order_items oi
            WHERE oi.vendor_user_id IS NOT NULL
            GROUP BY oi.order_id, oi.vendor_user_id
        ) AS grouped
        JOIN orders o ON o.id = grouped.order_id
        LEFT JOIN stores s ON s.id = grouped.store_id
        """
    )


def downgrade() -> None:
    op.drop_index("ix_order_packages_auto_release_due", table_name="order_packages")
    op.drop_index("ix_order_packages_settlement_status", table_name="order_packages")
    op.drop_index("ix_order_packages_delivery_status", table_name="order_packages")
    op.drop_index("ix_order_packages_vendor_status", table_name="order_packages")
    op.drop_index("ix_order_packages_store_id", table_name="order_packages")
    op.drop_index("ix_order_packages_vendor_user_id", table_name="order_packages")
    op.drop_index("ix_order_packages_order_id", table_name="order_packages")
    op.drop_table("order_packages")
    op.drop_column("vendor_wallet_transactions", "delivery_fee_amount")
    op.drop_column("stores", "same_day_delivery_enabled")
    op.drop_column("stores", "express_delivery_enabled")
    op.drop_column("stores", "free_delivery_threshold")
    op.drop_column("stores", "delivery_fee_same_day")
    op.drop_column("stores", "delivery_fee_express")
    op.drop_column("stores", "delivery_fee_economy")
