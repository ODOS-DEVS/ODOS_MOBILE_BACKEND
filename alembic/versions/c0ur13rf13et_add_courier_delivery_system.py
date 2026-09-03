"""Add couriers, delivery offers, and courier wallets

Revision ID: c0ur13rf13et
Revises: s70r3s3ct10n

Additive only. New tables (couriers, courier_applications, delivery_offers,
courier_wallets, courier_wallet_transactions, courier_withdrawal_requests) and
new nullable columns on users and orders. Nothing existing is altered, and
every new Order/User column defaults to null/none, so no row currently in
production changes meaning.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c0ur13rf13et"
down_revision = "s70r3s3ct10n"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {col["name"] for col in _inspector().get_columns(table)}


# create_type=False on both: the types are created explicitly below via
# IF NOT EXISTS, matching the established pattern in this project (see
# 17b4d0b9f1a2). postgresql.ENUM(...).create(checkfirst=True) is not reliable
# here -- it raised DuplicateObject on a database that pg_type confirmed did
# not have the type, which rolled back the whole migration under Alembic's
# transactional DDL.
courier_status_enum = postgresql.ENUM(
    "none", "pending", "under_review", "approved", "rejected", "suspended",
    name="courier_status", create_type=False,
)
courier_vehicle_type_enum = postgresql.ENUM(
    "on_foot", "bike", "motorbike", "car", "van",
    name="courier_vehicle_type", create_type=False,
)


def upgrade() -> None:
    tables = _tables()

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'courier_status') THEN
                CREATE TYPE courier_status AS ENUM
                    ('none', 'pending', 'under_review', 'approved', 'rejected', 'suspended');
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'courier_vehicle_type') THEN
                CREATE TYPE courier_vehicle_type AS ENUM
                    ('on_foot', 'bike', 'motorbike', 'car', 'van');
            END IF;
        END
        $$;
        """
    )

    if "courier_status" not in _columns("users"):
        op.add_column(
            "users",
            sa.Column(
                "courier_status",
                courier_status_enum,
                nullable=False,
                server_default="none",
            ),
        )
    if "courier_rejection_reason" not in _columns("users"):
        op.add_column(
            "users", sa.Column("courier_rejection_reason", sa.String(length=255), nullable=True)
        )

    if "courier_applications" not in tables:
        op.create_table(
            "courier_applications",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("status", courier_status_enum, nullable=False, server_default="pending"),
            sa.Column("full_name", sa.String(length=160), nullable=False),
            sa.Column("phone_number", sa.String(length=30), nullable=False),
            sa.Column(
                "vendor_id", sa.String(length=50),
                sa.ForeignKey("stores.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("vehicle_type", courier_vehicle_type_enum, nullable=False),
            sa.Column("plate_number", sa.String(length=30), nullable=True),
            sa.Column("ghana_card_number", sa.String(length=60), nullable=True),
            sa.Column("id_document_url", sa.String(length=500), nullable=True),
            sa.Column("region", sa.String(length=120), nullable=False),
            sa.Column("city", sa.String(length=120), nullable=False),
            sa.Column("rejection_reason", sa.String(length=255), nullable=True),
            sa.Column(
                "reviewed_by_user_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True),
                server_default=sa.text("now()"), nullable=False,
            ),
            sa.UniqueConstraint("user_id", name="uq_courier_applications_user_id"),
        )
        op.create_index("ix_courier_applications_user_id", "courier_applications", ["user_id"])
        op.create_index("ix_courier_applications_vendor_id", "courier_applications", ["vendor_id"])

    if "couriers" not in tables:
        op.create_table(
            "couriers",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True,
            ),
            sa.Column(
                "vendor_id", sa.String(length=50),
                sa.ForeignKey("stores.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("vehicle_type", courier_vehicle_type_enum, nullable=False),
            sa.Column("plate_number", sa.String(length=30), nullable=True),
            sa.Column("is_online", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("current_latitude", sa.Float(), nullable=True),
            sa.Column("current_longitude", sa.Float(), nullable=True),
            sa.Column("location_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rating", sa.Float(), nullable=True),
            sa.Column("total_deliveries", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True),
                server_default=sa.text("now()"), nullable=False,
            ),
        )
        op.create_index("ix_couriers_user_id", "couriers", ["user_id"])
        op.create_index("ix_couriers_vendor_id", "couriers", ["vendor_id"])

    if "courier_wallets" not in tables:
        op.create_table(
            "courier_wallets",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "courier_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("couriers.id", ondelete="CASCADE"), nullable=False, unique=True,
            ),
            sa.Column("currency", sa.String(length=10), nullable=False, server_default="GHS"),
            sa.Column("available_balance", sa.Float(), nullable=False, server_default="0"),
            sa.Column("pending_withdrawal_balance", sa.Float(), nullable=False, server_default="0"),
            sa.Column("lifetime_earnings", sa.Float(), nullable=False, server_default="0"),
            sa.Column("total_withdrawn", sa.Float(), nullable=False, server_default="0"),
            sa.Column("payout_method_type", sa.String(length=30), nullable=True),
            sa.Column("payout_account_name", sa.String(length=120), nullable=True),
            sa.Column("payout_account_number", sa.String(length=80), nullable=True),
            sa.Column("payout_provider", sa.String(length=120), nullable=True),
            sa.Column("payout_provider_code", sa.String(length=40), nullable=True),
            sa.Column("paystack_recipient_code", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True),
                server_default=sa.text("now()"), nullable=False,
            ),
        )
        op.create_index("ix_courier_wallets_courier_id", "courier_wallets", ["courier_id"])
        op.create_index(
            "ix_courier_wallets_paystack_recipient_code", "courier_wallets", ["paystack_recipient_code"]
        )

    if "courier_withdrawal_requests" not in tables:
        op.create_table(
            "courier_withdrawal_requests",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "wallet_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("courier_wallets.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "courier_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("couriers.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("note", sa.String(length=500), nullable=True),
            sa.Column("admin_note", sa.String(length=500), nullable=True),
            sa.Column("payout_method_type", sa.String(length=30), nullable=False),
            sa.Column("payout_account_name", sa.String(length=120), nullable=False),
            sa.Column("payout_account_number", sa.String(length=80), nullable=False),
            sa.Column("payout_provider", sa.String(length=120), nullable=True),
            sa.Column("payout_provider_code", sa.String(length=40), nullable=True),
            sa.Column("paystack_recipient_code", sa.String(length=120), nullable=True),
            sa.Column("paystack_transfer_reference", sa.String(length=80), nullable=True, unique=True),
            sa.Column("paystack_transfer_code", sa.String(length=120), nullable=True),
            sa.Column("paystack_transfer_id", sa.String(length=120), nullable=True),
            sa.Column("transfer_initiated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("transfer_failure_reason", sa.String(length=255), nullable=True),
            sa.Column(
                "reviewed_by_user_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column(
                "updated_at", sa.DateTime(timezone=True),
                server_default=sa.text("now()"), nullable=False,
            ),
        )
        op.create_index(
            "ix_courier_withdrawal_requests_wallet_id", "courier_withdrawal_requests", ["wallet_id"]
        )
        op.create_index(
            "ix_courier_withdrawal_requests_courier_id", "courier_withdrawal_requests", ["courier_id"]
        )

    if "courier_wallet_transactions" not in tables:
        op.create_table(
            "courier_wallet_transactions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "wallet_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("courier_wallets.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "courier_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("couriers.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "order_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column(
                "withdrawal_request_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("courier_withdrawal_requests.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("balance_after", sa.Float(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint(
                "courier_id", "withdrawal_request_id", "kind",
                name="uq_courier_wallet_tx_courier_withdrawal_kind",
            ),
        )
        op.create_index("ix_courier_wallet_tx_wallet_id", "courier_wallet_transactions", ["wallet_id"])
        op.create_index("ix_courier_wallet_tx_courier_id", "courier_wallet_transactions", ["courier_id"])
        op.create_index("ix_courier_wallet_tx_order_id", "courier_wallet_transactions", ["order_id"])
        op.create_index(
            "ix_courier_wallet_tx_withdrawal_id", "courier_wallet_transactions", ["withdrawal_request_id"]
        )
        op.create_index("ix_courier_wallet_tx_kind", "courier_wallet_transactions", ["kind"])
        op.create_index(
            "uq_courier_wallet_tx_courier_order_kind",
            "courier_wallet_transactions",
            ["courier_id", "order_id", "kind"],
            unique=True,
            postgresql_where=sa.text("order_id IS NOT NULL"),
        )

    if "delivery_offers" not in tables:
        op.create_table(
            "delivery_offers",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "order_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
            ),
            sa.Column(
                "vendor_id", sa.String(length=50),
                sa.ForeignKey("stores.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
            sa.Column(
                "claimed_by_courier_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("couriers.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "assigned_by_admin_user_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
            ),
            sa.Column("sla_deadline", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.UniqueConstraint("order_id", name="uq_delivery_offers_order_id"),
        )
        op.create_index("ix_delivery_offers_status_vendor", "delivery_offers", ["status", "vendor_id"])

    if "courier_id" not in _columns("orders"):
        op.add_column(
            "orders",
            sa.Column(
                "courier_id", postgresql.UUID(as_uuid=True),
                sa.ForeignKey("couriers.id", ondelete="SET NULL"), nullable=True,
            ),
        )
        op.create_index("ix_orders_courier_id", "orders", ["courier_id"])
    if "courier_assigned_at" not in _columns("orders"):
        op.add_column("orders", sa.Column("courier_assigned_at", sa.DateTime(timezone=True), nullable=True))
    if "courier_picked_up_at" not in _columns("orders"):
        op.add_column("orders", sa.Column("courier_picked_up_at", sa.DateTime(timezone=True), nullable=True))
    if "courier_delivered_at" not in _columns("orders"):
        op.add_column("orders", sa.Column("courier_delivered_at", sa.DateTime(timezone=True), nullable=True))
    if "delivery_proof_image_url" not in _columns("orders"):
        op.add_column("orders", sa.Column("delivery_proof_image_url", sa.String(length=500), nullable=True))
    if "delivery_proof_note" not in _columns("orders"):
        op.add_column("orders", sa.Column("delivery_proof_note", sa.String(length=280), nullable=True))


def downgrade() -> None:
    op.drop_table("delivery_offers")
    op.drop_table("courier_wallet_transactions")
    op.drop_table("courier_withdrawal_requests")
    op.drop_table("courier_wallets")
    op.drop_table("couriers")
    op.drop_table("courier_applications")
    for col in (
        "courier_id", "courier_assigned_at", "courier_picked_up_at",
        "courier_delivered_at", "delivery_proof_image_url", "delivery_proof_note",
    ):
        op.drop_column("orders", col)
    op.drop_column("users", "courier_rejection_reason")
    op.drop_column("users", "courier_status")
    postgresql.ENUM(name="courier_vehicle_type").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="courier_status").drop(op.get_bind(), checkfirst=True)
