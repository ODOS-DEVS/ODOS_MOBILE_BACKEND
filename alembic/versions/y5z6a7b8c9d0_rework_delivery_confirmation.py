"""Rework delivery confirmation: drop the vendor-facing delivery code (the
vendor could always read it off their own screen, so it never actually
proved a handoff) and add reminder tracking for the customer-confirm /
auto-release flow that replaces it."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "y5z6a7b8c9d0"
down_revision = "x4y5z6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("orders")}
    if "delivery_reminder_sent_at" not in columns:
        op.add_column(
            "orders",
            sa.Column("delivery_reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "delivery_code" in columns:
        op.drop_column("orders", "delivery_code")


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("orders")}
    if "delivery_code" not in columns:
        op.add_column("orders", sa.Column("delivery_code", sa.String(length=8), nullable=True))
    if "delivery_reminder_sent_at" in columns:
        op.drop_column("orders", "delivery_reminder_sent_at")
