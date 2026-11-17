"""Let a payment belong to a booking as well as an order.

`payment_transactions` was built 1:1 against `orders`: `order_id` NOT NULL
with a unique index. Services need two things that shape cannot express —
a payment attached to a booking rather than an order, and a deposit followed
by a balance, which is two payments against one booking.

Rather than stand up a second payment system (and a second reconciliation
path, a second webhook handler and a second ledger), this widens the existing
table to carry either parent:

  * `order_id` becomes nullable, with a *partial* unique index so the old
    one-payment-per-order rule still holds for every order exactly as before.
  * `booking_id` is added, deliberately NOT unique, because deposit and
    balance are two rows for one booking.
  * A check constraint makes the row point at exactly one parent, so an
    orphaned or double-parented payment cannot be written.

Existing rows and existing code paths are untouched: every current
transaction has an order_id and keeps it.

Revision ID: j4y5g62y4g62
Revises: i3x4f51x3f51
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "j4y5g62y4g62"
down_revision = "i3x4f51x3f51"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("payment_transactions")}

    if "booking_id" in columns:
        return

    op.alter_column("payment_transactions", "order_id", existing_type=postgresql.UUID(), nullable=True)

    op.add_column(
        "payment_transactions",
        sa.Column(
            "booking_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bookings.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_payment_transactions_booking_id", "payment_transactions", ["booking_id"]
    )

    # Which part of a booking this payment settles. Null on order payments.
    op.add_column(
        "payment_transactions",
        sa.Column("purpose", sa.String(length=20), nullable=True),
    )

    # The old unique index assumed order_id was always present. A partial
    # index keeps that guarantee for orders while allowing many NULLs for
    # booking payments -- note Postgres would allow multiple NULLs under the
    # original index too, but being explicit documents the intent.
    op.drop_index("ix_payment_transactions_order_id", table_name="payment_transactions")
    op.create_index(
        "ix_payment_transactions_order_id",
        "payment_transactions",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )

    op.create_check_constraint(
        "ck_payment_transactions_single_parent",
        "payment_transactions",
        "(order_id IS NOT NULL AND booking_id IS NULL) "
        "OR (order_id IS NULL AND booking_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_payment_transactions_purpose",
        "payment_transactions",
        "purpose IS NULL OR purpose IN ('deposit','balance','full')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_payment_transactions_purpose", "payment_transactions", type_="check"
    )
    op.drop_constraint(
        "ck_payment_transactions_single_parent", "payment_transactions", type_="check"
    )
    # Booking payments have no home in the old shape; drop them rather than
    # leave rows that violate the restored NOT NULL.
    op.execute("DELETE FROM payment_transactions WHERE booking_id IS NOT NULL")
    op.drop_index("ix_payment_transactions_booking_id", table_name="payment_transactions")
    op.drop_column("payment_transactions", "purpose")
    op.drop_column("payment_transactions", "booking_id")
    op.drop_index("ix_payment_transactions_order_id", table_name="payment_transactions")
    op.create_index(
        "ix_payment_transactions_order_id", "payment_transactions", ["order_id"], unique=True
    )
    op.alter_column(
        "payment_transactions", "order_id", existing_type=postgresql.UUID(), nullable=False
    )
