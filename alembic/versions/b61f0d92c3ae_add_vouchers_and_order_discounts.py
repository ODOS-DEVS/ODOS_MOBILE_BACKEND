"""add vouchers and order discounts

Revision ID: b61f0d92c3ae
Revises: ab12d4f6e8c9
Create Date: 2026-05-11 23:20:00.000000
"""

from datetime import datetime, timezone
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "b61f0d92c3ae"
down_revision = "ab12d4f6e8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vouchers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("issuer_name", sa.String(length=120), nullable=True),
        sa.Column("reward_text", sa.String(length=80), nullable=False),
        sa.Column("discount_type", sa.String(length=20), nullable=False),
        sa.Column("discount_value", sa.Float(), nullable=False),
        sa.Column("min_subtotal", sa.Float(), server_default="0", nullable=False),
        sa.Column("max_discount", sa.Float(), nullable=True),
        sa.Column("usage_limit", sa.Integer(), nullable=True),
        sa.Column("per_user_limit", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_vouchers_code"), "vouchers", ["code"], unique=True)

    op.add_column("orders", sa.Column("voucher_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("orders", sa.Column("voucher_code", sa.String(length=40), nullable=True))
    op.add_column("orders", sa.Column("voucher_title", sa.String(length=120), nullable=True))
    op.add_column("orders", sa.Column("discount_amount", sa.Float(), server_default="0", nullable=False))
    op.create_index(op.f("ix_orders_voucher_id"), "orders", ["voucher_id"], unique=False)
    op.create_foreign_key(
        "fk_orders_voucher_id_vouchers",
        "orders",
        "vouchers",
        ["voucher_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "voucher_redemptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voucher_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voucher_code", sa.String(length=40), nullable=False),
        sa.Column("discount_amount", sa.Float(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voucher_id"], ["vouchers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
    )
    op.create_index(
        op.f("ix_voucher_redemptions_order_id"),
        "voucher_redemptions",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voucher_redemptions_user_id"),
        "voucher_redemptions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voucher_redemptions_voucher_id"),
        "voucher_redemptions",
        ["voucher_id"],
        unique=False,
    )

    vouchers_table = sa.table(
        "vouchers",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String(length=40)),
        sa.column("title", sa.String(length=120)),
        sa.column("description", sa.String(length=255)),
        sa.column("issuer_name", sa.String(length=120)),
        sa.column("reward_text", sa.String(length=80)),
        sa.column("discount_type", sa.String(length=20)),
        sa.column("discount_value", sa.Float()),
        sa.column("min_subtotal", sa.Float()),
        sa.column("max_discount", sa.Float()),
        sa.column("usage_limit", sa.Integer()),
        sa.column("per_user_limit", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("starts_at", sa.DateTime(timezone=True)),
        sa.column("ends_at", sa.DateTime(timezone=True)),
    )

    now = datetime.now(timezone.utc)
    op.bulk_insert(
        vouchers_table,
        [
            {
                "id": uuid.uuid4(),
                "code": "WELCOME10",
                "title": "Welcome savings",
                "description": "A simple first-purchase voucher for new ODOS checkouts.",
                "issuer_name": "ODOS",
                "reward_text": "10% OFF",
                "discount_type": "percent",
                "discount_value": 10,
                "min_subtotal": 100,
                "max_discount": 60,
                "usage_limit": 1000,
                "per_user_limit": 1,
                "is_active": True,
                "starts_at": now,
                "ends_at": None,
            },
            {
                "id": uuid.uuid4(),
                "code": "ODOS25",
                "title": "Quick basket boost",
                "description": "Take GHS 25 off when the basket is ready for checkout.",
                "issuer_name": "ODOS",
                "reward_text": "GHS 25 OFF",
                "discount_type": "fixed",
                "discount_value": 25,
                "min_subtotal": 150,
                "max_discount": None,
                "usage_limit": 500,
                "per_user_limit": 1,
                "is_active": True,
                "starts_at": now,
                "ends_at": None,
            },
            {
                "id": uuid.uuid4(),
                "code": "STYLE15",
                "title": "Bigger basket reward",
                "description": "A stronger discount for larger carts and premium items.",
                "issuer_name": "ODOS",
                "reward_text": "15% OFF",
                "discount_type": "percent",
                "discount_value": 15,
                "min_subtotal": 250,
                "max_discount": 90,
                "usage_limit": 300,
                "per_user_limit": 1,
                "is_active": True,
                "starts_at": now,
                "ends_at": None,
            },
            {
                "id": uuid.uuid4(),
                "code": "ARCHIVE5",
                "title": "Archive voucher",
                "description": "An older campaign saved in the wallet for status examples.",
                "issuer_name": "ODOS",
                "reward_text": "GHS 5 OFF",
                "discount_type": "fixed",
                "discount_value": 5,
                "min_subtotal": 50,
                "max_discount": None,
                "usage_limit": 100,
                "per_user_limit": 1,
                "is_active": True,
                "starts_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "ends_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
            },
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_voucher_redemptions_voucher_id"), table_name="voucher_redemptions")
    op.drop_index(op.f("ix_voucher_redemptions_user_id"), table_name="voucher_redemptions")
    op.drop_index(op.f("ix_voucher_redemptions_order_id"), table_name="voucher_redemptions")
    op.drop_table("voucher_redemptions")

    op.drop_constraint("fk_orders_voucher_id_vouchers", "orders", type_="foreignkey")
    op.drop_index(op.f("ix_orders_voucher_id"), table_name="orders")
    op.drop_column("orders", "discount_amount")
    op.drop_column("orders", "voucher_title")
    op.drop_column("orders", "voucher_code")
    op.drop_column("orders", "voucher_id")

    op.drop_index(op.f("ix_vouchers_code"), table_name="vouchers")
    op.drop_table("vouchers")
