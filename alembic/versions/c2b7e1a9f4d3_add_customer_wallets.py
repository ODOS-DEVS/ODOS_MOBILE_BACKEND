"""add customer wallets

Revision ID: c2b7e1a9f4d3
Revises: f1a2b3c4d5e6
Create Date: 2026-06-02 14:20:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2b7e1a9f4d3"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_wallets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default="GHS", nullable=False),
        sa.Column("available_balance", sa.Float(), server_default="0", nullable=False),
        sa.Column("lifetime_topups", sa.Float(), server_default="0", nullable=False),
        sa.Column("lifetime_spend", sa.Float(), server_default="0", nullable=False),
        sa.Column("lifetime_refunds", sa.Float(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_customer_wallets_user_id", "customer_wallets", ["user_id"], unique=True)

    op.create_table(
        "customer_wallet_topups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=30), server_default="paystack", nullable=False),
        sa.Column("reference", sa.String(length=80), nullable=False),
        sa.Column("access_code", sa.String(length=120), nullable=True),
        sa.Column("authorization_url", sa.String(length=500), nullable=True),
        sa.Column("amount_subunit", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default="GHS", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("provider_transaction_id", sa.String(length=80), nullable=True),
        sa.Column("gateway_response", sa.String(length=255), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["wallet_id"], ["customer_wallets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference"),
    )
    op.create_index("ix_customer_wallet_topups_reference", "customer_wallet_topups", ["reference"], unique=True)
    op.create_index("ix_customer_wallet_topups_wallet_id", "customer_wallet_topups", ["wallet_id"], unique=False)
    op.create_index("ix_customer_wallet_topups_user_id", "customer_wallet_topups", ["user_id"], unique=False)

    op.create_table(
        "customer_wallet_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("topup_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("balance_after", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["wallet_id"], ["customer_wallets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["topup_id"], ["customer_wallet_topups.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "order_id", "kind", name="uq_customer_wallet_tx_user_order_kind"),
        sa.UniqueConstraint("user_id", "topup_id", "kind", name="uq_customer_wallet_tx_user_topup_kind"),
    )
    op.create_index("ix_customer_wallet_transactions_wallet_id", "customer_wallet_transactions", ["wallet_id"], unique=False)
    op.create_index("ix_customer_wallet_transactions_user_id", "customer_wallet_transactions", ["user_id"], unique=False)
    op.create_index("ix_customer_wallet_transactions_order_id", "customer_wallet_transactions", ["order_id"], unique=False)
    op.create_index("ix_customer_wallet_transactions_topup_id", "customer_wallet_transactions", ["topup_id"], unique=False)
    op.create_index("ix_customer_wallet_transactions_kind", "customer_wallet_transactions", ["kind"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_customer_wallet_transactions_kind", table_name="customer_wallet_transactions")
    op.drop_index("ix_customer_wallet_transactions_topup_id", table_name="customer_wallet_transactions")
    op.drop_index("ix_customer_wallet_transactions_order_id", table_name="customer_wallet_transactions")
    op.drop_index("ix_customer_wallet_transactions_user_id", table_name="customer_wallet_transactions")
    op.drop_index("ix_customer_wallet_transactions_wallet_id", table_name="customer_wallet_transactions")
    op.drop_table("customer_wallet_transactions")

    op.drop_index("ix_customer_wallet_topups_user_id", table_name="customer_wallet_topups")
    op.drop_index("ix_customer_wallet_topups_wallet_id", table_name="customer_wallet_topups")
    op.drop_index("ix_customer_wallet_topups_reference", table_name="customer_wallet_topups")
    op.drop_table("customer_wallet_topups")

    op.drop_index("ix_customer_wallets_user_id", table_name="customer_wallets")
    op.drop_table("customer_wallets")
