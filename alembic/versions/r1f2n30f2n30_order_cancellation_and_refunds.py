"""Separate cancellation and refund from order status, and record refunds.

Cancelling an order and returning the money are different events that happen at
different times and can fail independently. The old code collapsed them: it set
`payment_status = "cancelled"`, which is not a payment state at all, and
destroyed the record of whether money had ever been taken. The dev database
carries fourteen orders in that condition, five of which were delivered.

Four dimensions now coexist, which is what lets the database state the truth:

    status=cancelled  payment_status=paid  cancellation_status=completed
    refund_status=processing

An order can be cancelled while its payment stays "paid" and its refund is
still in flight, because that is what is actually true.

`order_refunds` is a ledger, not a flag. KutanaPay exposes no refund API, so
money goes back by hand and the row records who confirmed it and with what
reference. Multiple rows per order are allowed on purpose: a partial
cancellation refunds part of an order, and a later one refunds more.

Every column is additive with a safe default, so existing orders read as
"no cancellation, no refund" and nothing is rewritten.

Revision ID: r1f2n30f2n30
Revises: q0e1m29e1m29
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "r1f2n30f2n30"
down_revision = "q0e1m29e1m29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    order_columns = {c["name"] for c in inspector.get_columns("orders")}

    def add(name: str, column: sa.Column) -> None:
        if name not in order_columns:
            op.add_column("orders", column)

    # --- Cancellation, as its own dimension -------------------------------
    add(
        "cancellation_status",
        sa.Column(
            "cancellation_status",
            sa.String(length=20),
            nullable=False,
            server_default="none",
        ),
    )
    add(
        "cancellation_requested_at",
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    add(
        "cancellation_requested_by",
        sa.Column(
            "cancellation_requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    #: "customer" | "vendor" | "admin" | "system" — who asked, independent of
    #: which account, so a support agent acting for a shopper is still visible
    #: as an admin action.
    add(
        "cancellation_requested_role",
        sa.Column("cancellation_requested_role", sa.String(length=16), nullable=True),
    )
    #: Structured reason, separate from the free-text `cancellation_reason`
    #: that already exists, so cancellations can be counted and compared.
    add(
        "cancellation_reason_code",
        sa.Column("cancellation_reason_code", sa.String(length=40), nullable=True),
    )
    add(
        "cancellation_decided_at",
        sa.Column("cancellation_decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    add(
        "cancellation_decided_by",
        sa.Column(
            "cancellation_decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    add(
        "cancellation_decision_note",
        sa.Column("cancellation_decision_note", sa.String(length=500), nullable=True),
    )

    # --- Refund, as its own dimension -------------------------------------
    add(
        "refund_status",
        sa.Column(
            "refund_status",
            sa.String(length=24),
            nullable=False,
            server_default="not_applicable",
        ),
    )

    indexes = {index["name"] for index in inspector.get_indexes("orders")}
    if "ix_orders_cancellation_status" not in indexes:
        # The vendor and admin cancellation queues read exactly this.
        op.create_index(
            "ix_orders_cancellation_status",
            "orders",
            ["cancellation_status"],
            postgresql_where=sa.text("cancellation_status <> 'none'"),
        )
    if "ix_orders_refund_status" not in indexes:
        op.create_index(
            "ix_orders_refund_status",
            "orders",
            ["refund_status"],
            postgresql_where=sa.text("refund_status <> 'not_applicable'"),
        )
    if "ix_orders_awaiting_payment" not in indexes:
        # The expiry sweeper's hot path: unpaid orders old enough to lapse.
        op.create_index(
            "ix_orders_awaiting_payment",
            "orders",
            ["status", "created_at"],
            postgresql_where=sa.text("status = 'pending_payment'"),
        )

    # --- The refund ledger -------------------------------------------------
    if "order_refunds" not in set(inspector.get_table_names()):
        op.create_table(
            "order_refunds",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("reference", sa.String(length=24), nullable=False, unique=True),
            sa.Column(
                "order_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orders.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            #: Null for a whole-order refund; set when only one vendor's part
            #: of a multi-vendor order is being returned.
            sa.Column(
                "vendor_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
                index=True,
            ),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="GHS"),
            #: How the figure was reached, kept so a shopper query years later
            #: can be answered without re-deriving it from prices that moved.
            sa.Column("items_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("delivery_amount", sa.Float(), nullable=False, server_default="0"),
            sa.Column("discount_withheld", sa.Float(), nullable=False, server_default="0"),
            sa.Column("gateway_fee_withheld", sa.Float(), nullable=False, server_default="0"),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="pending", index=True
            ),
            sa.Column("reason_code", sa.String(length=40), nullable=True),
            sa.Column("reason_note", sa.String(length=500), nullable=True),
            #: The provider's own reference once the money has actually moved.
            #: Required before a refund may be marked successful.
            sa.Column("provider_reference", sa.String(length=120), nullable=True),
            sa.Column("failure_reason", sa.String(length=500), nullable=True),
            sa.Column(
                "initiated_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "confirmed_by",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        # One open refund per scope at a time. A double-tapped "refund" button,
        # or two admins acting at once, is settled here rather than by a check
        # that both could pass.
        op.create_index(
            "uq_order_refund_open_scope",
            "order_refunds",
            ["order_id", "vendor_user_id"],
            unique=True,
            postgresql_where=sa.text("status IN ('pending', 'approved', 'processing')"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if "order_refunds" in set(inspector.get_table_names()):
        op.drop_table("order_refunds")

    indexes = {index["name"] for index in inspector.get_indexes("orders")}
    for name in (
        "ix_orders_awaiting_payment",
        "ix_orders_refund_status",
        "ix_orders_cancellation_status",
    ):
        if name in indexes:
            op.drop_index(name, table_name="orders")

    order_columns = {c["name"] for c in inspector.get_columns("orders")}
    for name in (
        "refund_status",
        "cancellation_decision_note",
        "cancellation_decided_by",
        "cancellation_decided_at",
        "cancellation_reason_code",
        "cancellation_requested_role",
        "cancellation_requested_by",
        "cancellation_requested_at",
        "cancellation_status",
    ):
        if name in order_columns:
            op.drop_column("orders", name)
