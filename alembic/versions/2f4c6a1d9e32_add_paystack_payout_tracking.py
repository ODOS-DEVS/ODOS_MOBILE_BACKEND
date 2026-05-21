"""add paystack payout tracking

Revision ID: 2f4c6a1d9e32
Revises: 74830cffdf61
Create Date: 2026-05-19 16:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "2f4c6a1d9e32"
down_revision = "74830cffdf61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vendor_wallets",
        sa.Column("payout_provider_code", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "vendor_wallets",
        sa.Column("paystack_recipient_code", sa.String(length=120), nullable=True),
    )
    op.create_index(
        op.f("ix_vendor_wallets_paystack_recipient_code"),
        "vendor_wallets",
        ["paystack_recipient_code"],
        unique=False,
    )

    op.add_column(
        "vendor_withdrawal_requests",
        sa.Column("payout_provider_code", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "vendor_withdrawal_requests",
        sa.Column("paystack_recipient_code", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "vendor_withdrawal_requests",
        sa.Column("paystack_transfer_reference", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "vendor_withdrawal_requests",
        sa.Column("paystack_transfer_code", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "vendor_withdrawal_requests",
        sa.Column("paystack_transfer_id", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "vendor_withdrawal_requests",
        sa.Column("transfer_initiated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vendor_withdrawal_requests",
        sa.Column("transfer_failure_reason", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_vendor_withdrawal_requests_paystack_transfer_reference",
        "vendor_withdrawal_requests",
        ["paystack_transfer_reference"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_vendor_withdrawal_requests_paystack_transfer_reference",
        "vendor_withdrawal_requests",
        type_="unique",
    )
    op.drop_column("vendor_withdrawal_requests", "transfer_failure_reason")
    op.drop_column("vendor_withdrawal_requests", "transfer_initiated_at")
    op.drop_column("vendor_withdrawal_requests", "paystack_transfer_id")
    op.drop_column("vendor_withdrawal_requests", "paystack_transfer_code")
    op.drop_column("vendor_withdrawal_requests", "paystack_transfer_reference")
    op.drop_column("vendor_withdrawal_requests", "paystack_recipient_code")
    op.drop_column("vendor_withdrawal_requests", "payout_provider_code")

    op.drop_index(
        op.f("ix_vendor_wallets_paystack_recipient_code"),
        table_name="vendor_wallets",
    )
    op.drop_column("vendor_wallets", "paystack_recipient_code")
    op.drop_column("vendor_wallets", "payout_provider_code")
