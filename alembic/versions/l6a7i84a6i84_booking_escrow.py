"""Hold booking payments until the work is confirmed; drop deposits.

Two decisions land together because they are the same decision. Odos now
takes the full amount at booking and holds it, so there is no deposit to
configure and no balance to chase. The customer's protection comes from the
hold rather than from paying part of the price.

Escrow columns mirror `order_packages.auto_release_at`, so service money
settles the way product money already does.

Revision ID: l6a7i84a6i84
Revises: k5z6h73z5h73
Create Date: 2026-11-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "l6a7i84a6i84"
down_revision = "k5z6h73z5h73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    booking_columns = {c["name"] for c in inspector.get_columns("bookings")}

    if "auto_release_at" not in booking_columns:
        op.add_column("bookings", sa.Column("work_completed_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("bookings", sa.Column("customer_confirmed_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("bookings", sa.Column("auto_release_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column("bookings", sa.Column("earnings_release_reason", sa.String(length=30), nullable=True))
        # The sweeper queries exactly this shape; without the index it would
        # scan every booking ever made on each run.
        op.create_index(
            "ix_bookings_pending_release",
            "bookings",
            ["auto_release_at"],
            postgresql_where=sa.text("earnings_released_at IS NULL"),
        )

    # No partial payments. Any service configured for a deposit or for
    # payment after the work becomes a full-payment service; both were
    # advertised as deposit-friendly, and the hold now does that job.
    op.execute(
        "UPDATE services SET payment_timing = 'upfront' "
        "WHERE payment_timing IN ('deposit','on_completion')"
    )
    op.execute("UPDATE services SET deposit_percent = NULL")

    op.drop_constraint("ck_services_payment_timing", "services", type_="check")
    op.create_check_constraint(
        "ck_services_payment_timing",
        "services",
        "payment_timing IN ('upfront','free')",
    )

    # Bookings taken under the old rules keep their recorded deposit figure
    # for audit; the column simply stops being written. Dropping it would
    # destroy the record of what a past customer was actually asked for.


def downgrade() -> None:
    op.drop_constraint("ck_services_payment_timing", "services", type_="check")
    op.create_check_constraint(
        "ck_services_payment_timing",
        "services",
        "payment_timing IN ('upfront','deposit','on_completion','free')",
    )
    op.drop_index("ix_bookings_pending_release", table_name="bookings")
    for column in (
        "earnings_release_reason",
        "auto_release_at",
        "customer_confirmed_at",
        "work_completed_at",
    ):
        op.execute(f"ALTER TABLE bookings DROP COLUMN IF EXISTS {column}")
