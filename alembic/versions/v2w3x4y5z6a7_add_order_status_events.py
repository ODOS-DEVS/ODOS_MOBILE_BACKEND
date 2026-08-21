"""Add order_status_events table and orders.delivery_code for the unified
delivery timeline / proof-of-delivery feature."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "v2w3x4y5z6a7"
down_revision = "u1v2w3x4y5z6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    order_columns = {col["name"] for col in inspector.get_columns("orders")}
    if "delivery_code" not in order_columns:
        op.add_column(
            "orders",
            sa.Column("delivery_code", sa.String(length=8), nullable=True),
        )

    if "order_status_events" not in inspector.get_table_names():
        op.create_table(
            "order_status_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("actor_role", sa.String(length=20), nullable=False),
            sa.Column("note", sa.String(length=255), nullable=True),
            sa.Column(
                "occurred_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_order_status_events_order_id"),
            "order_status_events",
            ["order_id"],
            unique=False,
        )
        op.create_index(
            "ix_order_status_events_order_occurred",
            "order_status_events",
            ["order_id", "occurred_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if "order_status_events" in inspector.get_table_names():
        op.drop_index(
            "ix_order_status_events_order_occurred",
            table_name="order_status_events",
        )
        op.drop_index(
            op.f("ix_order_status_events_order_id"),
            table_name="order_status_events",
        )
        op.drop_table("order_status_events")

    order_columns = {col["name"] for col in inspector.get_columns("orders")}
    if "delivery_code" in order_columns:
        op.drop_column("orders", "delivery_code")
