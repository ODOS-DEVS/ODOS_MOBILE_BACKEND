"""Introduce a dedicated delivery/settlement sub-state machine on orders,
richer audit metadata on order_status_events, and a database-level
uniqueness guarantee against double-settling a vendor for the same order.

Backfills existing rows so in-flight and historical orders land in a
coherent state under the new columns rather than defaulting to
"not_dispatched" / "not_eligible" regardless of their real history.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "z6a7b8c9d0e1"
down_revision = "y5z6a7b8c9d0"
branch_labels = None
depends_on = None

NEW_ORDER_COLUMNS = [
    # SQLAlchemy auto-quotes plain-string server_default values as SQL string
    # literals — do not pre-quote these here or the default gets double-quoted.
    ("delivery_status", sa.String(length=30), "not_dispatched"),
    ("dispatched_at", sa.DateTime(timezone=True), None),
    ("dispatch_attempt_count", sa.Integer(), "0"),
    ("confirmation_method", sa.String(length=20), None),
    ("delivery_problem_reason", sa.String(length=500), None),
    ("delivery_problem_reported_at", sa.DateTime(timezone=True), None),
    ("auto_release_at", sa.DateTime(timezone=True), None),
    ("auto_released_at", sa.DateTime(timezone=True), None),
    ("settlement_status", sa.String(length=20), "not_eligible"),
]

SETTLEMENT_UNIQUE_INDEX = "ix_vendor_wallet_tx_unique_sale_settlement"


def upgrade() -> None:
    bind = op.get_bind()
    order_columns = {col["name"] for col in inspect(bind).get_columns("orders")}
    for name, col_type, server_default in NEW_ORDER_COLUMNS:
        if name not in order_columns:
            nullable = server_default is None
            op.add_column(
                "orders",
                sa.Column(name, col_type, nullable=nullable, server_default=server_default),
            )
    if "delivery_status" not in order_columns:
        op.create_index("ix_orders_delivery_status", "orders", ["delivery_status"])
    if "settlement_status" not in order_columns:
        op.create_index("ix_orders_settlement_status", "orders", ["settlement_status"])

    event_columns = {col["name"] for col in inspect(bind).get_columns("order_status_events")}
    if "actor_id" not in event_columns:
        op.add_column(
            "order_status_events",
            sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_order_status_events_actor_id_users",
            "order_status_events",
            "users",
            ["actor_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index("ix_order_status_events_actor_id", "order_status_events", ["actor_id"])
    if "event_metadata" not in event_columns:
        op.add_column(
            "order_status_events",
            sa.Column("event_metadata", postgresql.JSONB(), nullable=True),
        )

    # --- Backfill existing rows so history stays coherent under the new model ---
    if "delivery_status" not in order_columns:
        op.execute("UPDATE orders SET delivery_status = 'out_for_delivery' WHERE vendor_status = 'out_for_delivery'")
        op.execute("UPDATE orders SET delivery_status = 'delivered' WHERE status = 'delivered' OR vendor_status = 'delivered'")
        op.execute(
            """
            UPDATE orders o
            SET dispatched_at = sub.occurred_at
            FROM (
                SELECT DISTINCT ON (order_id) order_id, occurred_at
                FROM order_status_events
                WHERE status = 'out_for_delivery'
                ORDER BY order_id, occurred_at DESC
            ) sub
            WHERE o.id = sub.order_id AND o.delivery_status IN ('out_for_delivery', 'delivered')
            """
        )
        op.execute(
            """
            UPDATE orders o
            SET settlement_status = 'settled'
            WHERE EXISTS (
                SELECT 1 FROM vendor_wallet_transactions t
                WHERE t.order_id = o.id AND t.kind = 'sale_settlement'
            )
            """
        )
        op.execute(
            "UPDATE orders SET settlement_status = 'eligible' "
            "WHERE delivery_status = 'delivered' AND settlement_status != 'settled'"
        )
        op.execute(
            "UPDATE orders SET auto_release_at = dispatched_at + interval '48 hours' "
            "WHERE delivery_status = 'out_for_delivery' AND dispatched_at IS NOT NULL"
        )

    existing_indexes = {ix["name"] for ix in inspect(bind).get_indexes("vendor_wallet_transactions")}
    if SETTLEMENT_UNIQUE_INDEX not in existing_indexes:
        op.create_index(
            SETTLEMENT_UNIQUE_INDEX,
            "vendor_wallet_transactions",
            ["vendor_user_id", "order_id"],
            unique=True,
            postgresql_where=sa.text("kind = 'sale_settlement'"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing_indexes = {ix["name"] for ix in inspect(bind).get_indexes("vendor_wallet_transactions")}
    if SETTLEMENT_UNIQUE_INDEX in existing_indexes:
        op.drop_index(SETTLEMENT_UNIQUE_INDEX, table_name="vendor_wallet_transactions")

    event_columns = {col["name"] for col in inspect(bind).get_columns("order_status_events")}
    if "event_metadata" in event_columns:
        op.drop_column("order_status_events", "event_metadata")
    if "actor_id" in event_columns:
        op.drop_index("ix_order_status_events_actor_id", table_name="order_status_events")
        op.drop_constraint("fk_order_status_events_actor_id_users", "order_status_events", type_="foreignkey")
        op.drop_column("order_status_events", "actor_id")

    order_columns = {col["name"] for col in inspect(bind).get_columns("orders")}
    if "delivery_status" in order_columns:
        op.drop_index("ix_orders_delivery_status", table_name="orders")
    if "settlement_status" in order_columns:
        op.drop_index("ix_orders_settlement_status", table_name="orders")
    for name, _col_type, _default in reversed(NEW_ORDER_COLUMNS):
        if name in order_columns:
            op.drop_column("orders", name)
