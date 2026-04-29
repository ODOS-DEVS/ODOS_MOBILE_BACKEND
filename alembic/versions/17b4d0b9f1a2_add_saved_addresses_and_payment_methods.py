"""add saved addresses and payment methods

Revision ID: 17b4d0b9f1a2
Revises: cc7d2ea1b6a4
Create Date: 2026-04-29 18:35:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "17b4d0b9f1a2"
down_revision: str | None = "cc7d2ea1b6a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

payment_method_type_existing = postgresql.ENUM(
    "card", "momo", name="payment_method_type", create_type=False
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'payment_method_type') THEN
                CREATE TYPE payment_method_type AS ENUM ('card', 'momo');
            END IF;
        END
        $$;
        """
    )

    op.create_table(
        "saved_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("phone", sa.String(length=30), nullable=False),
        sa.Column("street", sa.String(length=255), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=120), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_saved_addresses_user_id"), "saved_addresses", ["user_id"], unique=False)

    op.create_table(
        "saved_payment_methods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", payment_method_type_existing, nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("card_name", sa.String(length=120), nullable=True),
        sa.Column("card_last4", sa.String(length=4), nullable=True),
        sa.Column("expiry", sa.String(length=10), nullable=True),
        sa.Column("network", sa.String(length=30), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_saved_payment_methods_user_id"), "saved_payment_methods", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_saved_payment_methods_user_id"), table_name="saved_payment_methods")
    op.drop_table("saved_payment_methods")
    op.drop_index(op.f("ix_saved_addresses_user_id"), table_name="saved_addresses")
    op.drop_table("saved_addresses")
    op.execute("DROP TYPE IF EXISTS payment_method_type")
