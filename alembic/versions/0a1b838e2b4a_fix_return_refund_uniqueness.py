"""Fix two return-refund correctness bugs at the database level:

1. `customer_wallet_transactions` and `vendor_wallet_transactions` each had a
   full (non-partial) unique constraint on (user/vendor, order_id, kind).
   For kind='credit_return' / 'refund_reversal' this is wrong — an order can
   have several items, each refunded via its own return request, so "one
   credit_return/refund_reversal row per order" rejected every refund on an
   order after the first with an IntegrityError. Narrowed to a partial index
   that excludes those two kinds, which are now correctly deduped by
   return_request_id instead (a real column, added here for the customer
   side to match the vendor side, which already had one).

2. Adds a partial unique index scoped to return_request_id for both tables —
   a database-level backstop against a double refund/reversal for the same
   return request, beneath the application-level row locking.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0a1b838e2b4a"
down_revision = "f81f085cfcda"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    customer_tx_columns = {
        col["name"] for col in inspect(bind).get_columns("customer_wallet_transactions")
    }
    if "return_request_id" not in customer_tx_columns:
        op.add_column(
            "customer_wallet_transactions",
            sa.Column(
                "return_request_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("return_requests.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_customer_wallet_transactions_return_request_id",
            "customer_wallet_transactions",
            ["return_request_id"],
        )
        # Backfill from the JSON metadata this column replaces as the
        # source of truth, so existing refund rows aren't silently orphaned.
        op.execute(
            """
            UPDATE customer_wallet_transactions
            SET return_request_id = (metadata_json->>'return_request_id')::uuid
            WHERE kind = 'credit_return'
              AND metadata_json->>'return_request_id' IS NOT NULL
            """
        )

    customer_tx_constraints = {
        c["name"] for c in inspect(bind).get_unique_constraints("customer_wallet_transactions")
    }
    if "uq_customer_wallet_tx_user_order_kind" in customer_tx_constraints:
        op.drop_constraint(
            "uq_customer_wallet_tx_user_order_kind",
            "customer_wallet_transactions",
            type_="unique",
        )
    customer_tx_indexes = {
        ix["name"] for ix in inspect(bind).get_indexes("customer_wallet_transactions")
    }
    if "uq_customer_wallet_tx_user_order_kind" not in customer_tx_indexes:
        op.create_index(
            "uq_customer_wallet_tx_user_order_kind",
            "customer_wallet_transactions",
            ["user_id", "order_id", "kind"],
            unique=True,
            postgresql_where=sa.text("kind != 'credit_return'"),
        )
    if "uq_customer_wallet_tx_return_request" not in customer_tx_indexes:
        op.create_index(
            "uq_customer_wallet_tx_return_request",
            "customer_wallet_transactions",
            ["return_request_id"],
            unique=True,
            postgresql_where=sa.text(
                "kind = 'credit_return' AND return_request_id IS NOT NULL"
            ),
        )

    vendor_tx_constraints = {
        c["name"] for c in inspect(bind).get_unique_constraints("vendor_wallet_transactions")
    }
    if "uq_vendor_wallet_tx_vendor_order_kind" in vendor_tx_constraints:
        op.drop_constraint(
            "uq_vendor_wallet_tx_vendor_order_kind",
            "vendor_wallet_transactions",
            type_="unique",
        )
    vendor_tx_indexes = {
        ix["name"] for ix in inspect(bind).get_indexes("vendor_wallet_transactions")
    }
    if "uq_vendor_wallet_tx_vendor_order_kind" not in vendor_tx_indexes:
        op.create_index(
            "uq_vendor_wallet_tx_vendor_order_kind",
            "vendor_wallet_transactions",
            ["vendor_user_id", "order_id", "kind"],
            unique=True,
            postgresql_where=sa.text("kind != 'refund_reversal'"),
        )


def downgrade() -> None:
    bind = op.get_bind()

    vendor_tx_indexes = {
        ix["name"] for ix in inspect(bind).get_indexes("vendor_wallet_transactions")
    }
    if "uq_vendor_wallet_tx_vendor_order_kind" in vendor_tx_indexes:
        op.drop_index("uq_vendor_wallet_tx_vendor_order_kind", table_name="vendor_wallet_transactions")
    op.create_unique_constraint(
        "uq_vendor_wallet_tx_vendor_order_kind",
        "vendor_wallet_transactions",
        ["vendor_user_id", "order_id", "kind"],
    )

    customer_tx_indexes = {
        ix["name"] for ix in inspect(bind).get_indexes("customer_wallet_transactions")
    }
    if "uq_customer_wallet_tx_return_request" in customer_tx_indexes:
        op.drop_index(
            "uq_customer_wallet_tx_return_request", table_name="customer_wallet_transactions"
        )
    if "uq_customer_wallet_tx_user_order_kind" in customer_tx_indexes:
        op.drop_index(
            "uq_customer_wallet_tx_user_order_kind", table_name="customer_wallet_transactions"
        )
    op.create_unique_constraint(
        "uq_customer_wallet_tx_user_order_kind",
        "customer_wallet_transactions",
        ["user_id", "order_id", "kind"],
    )

    customer_tx_columns = {
        col["name"] for col in inspect(bind).get_columns("customer_wallet_transactions")
    }
    if "return_request_id" in customer_tx_columns:
        op.drop_index(
            "ix_customer_wallet_transactions_return_request_id",
            table_name="customer_wallet_transactions",
        )
        op.drop_column("customer_wallet_transactions", "return_request_id")
