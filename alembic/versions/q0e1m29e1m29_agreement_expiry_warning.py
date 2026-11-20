"""Record when an agreement's expiry warning went out.

The reminder job runs every ten minutes and the warning window is three hours,
so without a marker a shopper would be told eighteen times that their price is
about to lapse.

A dedicated column rather than a sentinel value in `status`: five separate
places judge whether an agreement is still usable, and teaching all of them
that one extra status still counts as live is five chances to get it wrong in
code that decides what someone is charged.

Revision ID: q0e1m29e1m29
Revises: p9d0l18d0l18
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "q0e1m29e1m29"
down_revision = "p9d0l18d0l18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("negotiation_agreements")}
    if "expiry_warned_at" not in columns:
        op.add_column(
            "negotiation_agreements",
            sa.Column("expiry_warned_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("negotiation_agreements")}
    if "expiry_warned_at" in columns:
        op.drop_column("negotiation_agreements", "expiry_warned_at")
