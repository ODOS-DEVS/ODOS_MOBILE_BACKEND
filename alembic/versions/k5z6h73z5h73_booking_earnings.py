"""Attribute a vendor wallet movement to a booking.

`vendor_wallet_transactions` could only point at an order, so a provider paid
for a service would see a credit with no traceable source — and finance could
not answer "which booking produced this?" without reading the title string.
Nullable and additive: order payouts are unaffected.

Revision ID: k5z6h73z5h73
Revises: j4y5g62y4g62
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "k5z6h73z5h73"
down_revision = "j4y5g62y4g62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("vendor_wallet_transactions")}
    if "booking_id" in columns:
        return

    op.add_column(
        "vendor_wallet_transactions",
        sa.Column(
            "booking_id",
            postgresql.UUID(as_uuid=True),
            # SET NULL, not CASCADE: the provider was paid, and deleting the
            # booking must not erase the record of paying them.
            sa.ForeignKey("bookings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_vendor_wallet_transactions_booking_id",
        "vendor_wallet_transactions",
        ["booking_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vendor_wallet_transactions_booking_id",
        table_name="vendor_wallet_transactions",
    )
    op.drop_column("vendor_wallet_transactions", "booking_id")
