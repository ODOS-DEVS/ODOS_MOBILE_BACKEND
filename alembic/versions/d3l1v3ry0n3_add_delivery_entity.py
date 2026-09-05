"""Add the delivery entity: deliveries, delivery_events, delivery_attempts

Revision ID: d3l1v3ry0n3
Revises: c0ur13rr0l3

Additive except for one deliberate replacement: delivery_offers loses its
plain UNIQUE(order_id) and gains a partial unique index on (delivery_id)
WHERE status = 'open'.

The old constraint made an order offerable exactly once, ever -- an expired
offer or an abandoned claim left it permanently unofferable. The partial index
keeps the property that actually mattered (two riders can never race through
two different *open* rows) while letting closed offers accumulate as history.

Existing delivery_offers rows are backfilled with a delivery each, so the new
column is never silently null. In every environment this table is currently
empty, but the backfill costs nothing and makes the migration correct if it
isn't.

Nothing on orders or users changes. The vendor-dispatch flow is untouched.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d3l1v3ry0n3"
down_revision = "c0ur13rr0l3"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {col["name"] for col in _inspector().get_columns(table)}


def _indexes(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {ix["name"] for ix in _inspector().get_indexes(table)}


def _constraints(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {c["name"] for c in _inspector().get_unique_constraints(table)}


def upgrade() -> None:
    tables = _tables()

    if "deliveries" not in tables:
        op.create_table(
            "deliveries",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "order_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orders.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "vendor_id",
                sa.String(50),
                sa.ForeignKey("stores.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "courier_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("couriers.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "status",
                sa.String(30),
                nullable=False,
                server_default="pending_assignment",
            ),
            sa.Column("pickup_name", sa.String(160), nullable=True),
            sa.Column("pickup_address", sa.String(320), nullable=True),
            sa.Column("pickup_latitude", sa.Float(), nullable=True),
            sa.Column("pickup_longitude", sa.Float(), nullable=True),
            sa.Column("dropoff_area", sa.String(160), nullable=True),
            sa.Column("dropoff_address", sa.String(320), nullable=True),
            sa.Column("dropoff_latitude", sa.Float(), nullable=True),
            sa.Column("dropoff_longitude", sa.Float(), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failure_reason", sa.String(160), nullable=True),
            sa.Column("failure_note", sa.String(500), nullable=True),
            sa.Column("offered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("arrived_at_pickup_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("arrived_at_customer_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("returned_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )

    delivery_indexes = _indexes("deliveries")
    if "ix_deliveries_order_id" not in delivery_indexes:
        op.create_index("ix_deliveries_order_id", "deliveries", ["order_id"])
    if "ix_deliveries_vendor_id" not in delivery_indexes:
        op.create_index("ix_deliveries_vendor_id", "deliveries", ["vendor_id"])
    if "ix_deliveries_courier_id" not in delivery_indexes:
        op.create_index("ix_deliveries_courier_id", "deliveries", ["courier_id"])
    if "ix_deliveries_status" not in delivery_indexes:
        op.create_index("ix_deliveries_status", "deliveries", ["status"])
    if "ix_deliveries_courier_status" not in delivery_indexes:
        op.create_index(
            "ix_deliveries_courier_status", "deliveries", ["courier_id", "status"]
        )
    if "uq_deliveries_active_order" not in delivery_indexes:
        # One live delivery per order; terminal rows excluded so an order can
        # legitimately have a historical delivery plus a new one.
        op.create_index(
            "uq_deliveries_active_order",
            "deliveries",
            ["order_id"],
            unique=True,
            postgresql_where=sa.text(
                "status NOT IN ('delivered', 'returned', 'cancelled', 'disputed')"
            ),
        )

    if "delivery_events" not in tables:
        op.create_table(
            "delivery_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "delivery_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("deliveries.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event", sa.String(50), nullable=False),
            sa.Column("from_status", sa.String(30), nullable=True),
            sa.Column("to_status", sa.String(30), nullable=True),
            sa.Column("actor_type", sa.String(20), nullable=False),
            sa.Column(
                "actor_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("context", postgresql.JSONB(), nullable=True),
            sa.Column(
                "occurred_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    event_indexes = _indexes("delivery_events")
    if "ix_delivery_events_delivery_id" not in event_indexes:
        op.create_index(
            "ix_delivery_events_delivery_id", "delivery_events", ["delivery_id"]
        )
    if "ix_delivery_events_occurred_at" not in event_indexes:
        op.create_index(
            "ix_delivery_events_occurred_at", "delivery_events", ["occurred_at"]
        )

    if "delivery_attempts" not in tables:
        op.create_table(
            "delivery_attempts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "delivery_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("deliveries.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column(
                "outcome", sa.String(20), nullable=False, server_default="in_progress"
            ),
            sa.Column("failure_reason", sa.String(160), nullable=True),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "delivery_id",
                "attempt_number",
                name="uq_delivery_attempts_delivery_number",
            ),
        )
    if "ix_delivery_attempts_delivery_id" not in _indexes("delivery_attempts"):
        op.create_index(
            "ix_delivery_attempts_delivery_id", "delivery_attempts", ["delivery_id"]
        )

    # --- delivery_offers ---------------------------------------------------
    offer_columns = _columns("delivery_offers")
    if "delivery_id" not in offer_columns:
        op.add_column(
            "delivery_offers",
            sa.Column(
                "delivery_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("deliveries.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
    if "closed_reason" not in offer_columns:
        op.add_column(
            "delivery_offers", sa.Column("closed_reason", sa.String(20), nullable=True)
        )
    if "closed_at" not in offer_columns:
        op.add_column(
            "delivery_offers",
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Give every pre-existing offer a delivery, so delivery_id is meaningful
    # for every row rather than only for rows created after this migration.
    op.execute(
        sa.text(
            """
            INSERT INTO deliveries (
                id, order_id, vendor_id, courier_id, status,
                dropoff_area, dropoff_address, created_at, updated_at
            )
            SELECT
                gen_random_uuid(),
                o.order_id,
                o.vendor_id,
                o.claimed_by_courier_id,
                CASE WHEN o.status = 'claimed' THEN 'accepted' ELSE 'offered' END,
                concat_ws(', ', ord.address_city, ord.address_region),
                concat_ws(', ', ord.address_street, ord.address_city, ord.address_region),
                o.created_at,
                now()
            FROM delivery_offers o
            JOIN orders ord ON ord.id = o.order_id
            WHERE o.delivery_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE delivery_offers o
            SET delivery_id = d.id
            FROM deliveries d
            WHERE o.delivery_id IS NULL AND d.order_id = o.order_id
            """
        )
    )

    if "uq_delivery_offers_order_id" in _constraints("delivery_offers"):
        op.drop_constraint(
            "uq_delivery_offers_order_id", "delivery_offers", type_="unique"
        )

    if "uq_delivery_offers_open_per_delivery" not in _indexes("delivery_offers"):
        op.create_index(
            "uq_delivery_offers_open_per_delivery",
            "delivery_offers",
            ["delivery_id"],
            unique=True,
            postgresql_where=sa.text("status = 'open'"),
        )
    if "ix_delivery_offers_delivery_id" not in _indexes("delivery_offers"):
        op.create_index(
            "ix_delivery_offers_delivery_id", "delivery_offers", ["delivery_id"]
        )


def downgrade() -> None:
    offer_indexes = _indexes("delivery_offers")
    if "uq_delivery_offers_open_per_delivery" in offer_indexes:
        op.drop_index("uq_delivery_offers_open_per_delivery", table_name="delivery_offers")
    if "ix_delivery_offers_delivery_id" in offer_indexes:
        op.drop_index("ix_delivery_offers_delivery_id", table_name="delivery_offers")

    # Restoring the old unique constraint can only succeed if no order has more
    # than one offer -- which is exactly the limitation this migration removed.
    # Close all but the newest offer per order first so the downgrade is
    # possible rather than mysteriously failing.
    op.execute(
        sa.text(
            """
            DELETE FROM delivery_offers o
            USING delivery_offers newer
            WHERE o.order_id = newer.order_id
              AND o.created_at < newer.created_at
            """
        )
    )
    if "uq_delivery_offers_order_id" not in _constraints("delivery_offers"):
        op.create_unique_constraint(
            "uq_delivery_offers_order_id", "delivery_offers", ["order_id"]
        )

    offer_columns = _columns("delivery_offers")
    for column in ("closed_at", "closed_reason", "delivery_id"):
        if column in offer_columns:
            op.drop_column("delivery_offers", column)

    for table in ("delivery_attempts", "delivery_events", "deliveries"):
        if table in _tables():
            op.drop_table(table)
