"""add vendor wallets

Revision ID: d41a3c8e9b27
Revises: ce5f6b7a8d91
Create Date: 2026-05-18 10:55:00.000000
"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "d41a3c8e9b27"
down_revision: Union[str, None] = "ce5f6b7a8d91"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_items",
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "order_items",
        sa.Column("store_id", sa.String(length=50), nullable=True),
    )
    op.create_index(op.f("ix_order_items_vendor_user_id"), "order_items", ["vendor_user_id"], unique=False)
    op.create_index(op.f("ix_order_items_store_id"), "order_items", ["store_id"], unique=False)
    op.create_foreign_key(
        "fk_order_items_vendor_user_id_users",
        "order_items",
        "users",
        ["vendor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        """
        UPDATE order_items AS oi
        SET vendor_user_id = p.vendor_user_id,
            store_id = p.store_id
        FROM products AS p
        WHERE oi.product_id = p.id
        """
    )

    op.create_table(
        "vendor_wallets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default="GHS", nullable=False),
        sa.Column("available_balance", sa.Float(), server_default="0", nullable=False),
        sa.Column("pending_withdrawal_balance", sa.Float(), server_default="0", nullable=False),
        sa.Column("lifetime_earnings", sa.Float(), server_default="0", nullable=False),
        sa.Column("total_withdrawn", sa.Float(), server_default="0", nullable=False),
        sa.Column("total_commission", sa.Float(), server_default="0", nullable=False),
        sa.Column("payout_method_type", sa.String(length=30), nullable=True),
        sa.Column("payout_account_name", sa.String(length=120), nullable=True),
        sa.Column("payout_account_number", sa.String(length=80), nullable=True),
        sa.Column("payout_provider", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["vendor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vendor_user_id"),
    )
    op.create_index(op.f("ix_vendor_wallets_vendor_user_id"), "vendor_wallets", ["vendor_user_id"], unique=True)

    op.create_table(
        "vendor_withdrawal_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("admin_note", sa.String(length=255), nullable=True),
        sa.Column("payout_method_type", sa.String(length=30), nullable=False),
        sa.Column("payout_account_name", sa.String(length=120), nullable=False),
        sa.Column("payout_account_number", sa.String(length=80), nullable=False),
        sa.Column("payout_provider", sa.String(length=120), nullable=True),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vendor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_id"], ["vendor_wallets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_vendor_withdrawal_requests_wallet_id"), "vendor_withdrawal_requests", ["wallet_id"], unique=False)
    op.create_index(op.f("ix_vendor_withdrawal_requests_vendor_user_id"), "vendor_withdrawal_requests", ["vendor_user_id"], unique=False)
    op.create_index(op.f("ix_vendor_withdrawal_requests_reviewed_by_user_id"), "vendor_withdrawal_requests", ["reviewed_by_user_id"], unique=False)
    op.create_index(op.f("ix_vendor_withdrawal_requests_status"), "vendor_withdrawal_requests", ["status"], unique=False)

    op.create_table(
        "vendor_wallet_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("return_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("withdrawal_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("gross_amount", sa.Float(), nullable=True),
        sa.Column("commission_amount", sa.Float(), nullable=True),
        sa.Column("balance_after", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["return_request_id"], ["return_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["vendor_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["wallet_id"], ["vendor_wallets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["withdrawal_request_id"], ["vendor_withdrawal_requests.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vendor_user_id", "order_id", "kind", name="uq_vendor_wallet_tx_vendor_order_kind"),
        sa.UniqueConstraint("vendor_user_id", "return_request_id", "kind", name="uq_vendor_wallet_tx_vendor_return_kind"),
        sa.UniqueConstraint("vendor_user_id", "withdrawal_request_id", "kind", name="uq_vendor_wallet_tx_vendor_withdrawal_kind"),
    )
    op.create_index(op.f("ix_vendor_wallet_transactions_wallet_id"), "vendor_wallet_transactions", ["wallet_id"], unique=False)
    op.create_index(op.f("ix_vendor_wallet_transactions_vendor_user_id"), "vendor_wallet_transactions", ["vendor_user_id"], unique=False)
    op.create_index(op.f("ix_vendor_wallet_transactions_order_id"), "vendor_wallet_transactions", ["order_id"], unique=False)
    op.create_index(op.f("ix_vendor_wallet_transactions_return_request_id"), "vendor_wallet_transactions", ["return_request_id"], unique=False)
    op.create_index(op.f("ix_vendor_wallet_transactions_withdrawal_request_id"), "vendor_wallet_transactions", ["withdrawal_request_id"], unique=False)
    op.create_index(op.f("ix_vendor_wallet_transactions_kind"), "vendor_wallet_transactions", ["kind"], unique=False)

    connection = op.get_bind()
    approved_vendor_ids = [
        row[0]
        for row in connection.execute(
            sa.text("SELECT id FROM users WHERE vendor_status = 'approved'")
        ).all()
    ]
    if approved_vendor_ids:
        wallet_table = sa.table(
            "vendor_wallets",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("vendor_user_id", postgresql.UUID(as_uuid=True)),
            sa.column("currency", sa.String(length=10)),
            sa.column("available_balance", sa.Float()),
            sa.column("pending_withdrawal_balance", sa.Float()),
            sa.column("lifetime_earnings", sa.Float()),
            sa.column("total_withdrawn", sa.Float()),
            sa.column("total_commission", sa.Float()),
        )
        connection.execute(
            wallet_table.insert(),
            [
                {
                    "id": uuid.uuid4(),
                    "vendor_user_id": vendor_user_id,
                    "currency": "GHS",
                    "available_balance": 0,
                    "pending_withdrawal_balance": 0,
                    "lifetime_earnings": 0,
                    "total_withdrawn": 0,
                    "total_commission": 0,
                }
                for vendor_user_id in approved_vendor_ids
            ],
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_vendor_wallet_transactions_kind"), table_name="vendor_wallet_transactions")
    op.drop_index(op.f("ix_vendor_wallet_transactions_withdrawal_request_id"), table_name="vendor_wallet_transactions")
    op.drop_index(op.f("ix_vendor_wallet_transactions_return_request_id"), table_name="vendor_wallet_transactions")
    op.drop_index(op.f("ix_vendor_wallet_transactions_order_id"), table_name="vendor_wallet_transactions")
    op.drop_index(op.f("ix_vendor_wallet_transactions_vendor_user_id"), table_name="vendor_wallet_transactions")
    op.drop_index(op.f("ix_vendor_wallet_transactions_wallet_id"), table_name="vendor_wallet_transactions")
    op.drop_table("vendor_wallet_transactions")

    op.drop_index(op.f("ix_vendor_withdrawal_requests_status"), table_name="vendor_withdrawal_requests")
    op.drop_index(op.f("ix_vendor_withdrawal_requests_reviewed_by_user_id"), table_name="vendor_withdrawal_requests")
    op.drop_index(op.f("ix_vendor_withdrawal_requests_vendor_user_id"), table_name="vendor_withdrawal_requests")
    op.drop_index(op.f("ix_vendor_withdrawal_requests_wallet_id"), table_name="vendor_withdrawal_requests")
    op.drop_table("vendor_withdrawal_requests")

    op.drop_index(op.f("ix_vendor_wallets_vendor_user_id"), table_name="vendor_wallets")
    op.drop_table("vendor_wallets")

    op.drop_constraint("fk_order_items_vendor_user_id_users", "order_items", type_="foreignkey")
    op.drop_index(op.f("ix_order_items_store_id"), table_name="order_items")
    op.drop_index(op.f("ix_order_items_vendor_user_id"), table_name="order_items")
    op.drop_column("order_items", "store_id")
    op.drop_column("order_items", "vendor_user_id")
