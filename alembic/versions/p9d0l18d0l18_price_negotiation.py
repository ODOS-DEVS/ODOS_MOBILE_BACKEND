"""Price negotiation: negotiable products, offers, and private agreed prices.

Four additions, designed to bolt onto what already exists rather than beside it.

Negotiation settings live on `products` instead of a side table: `is_negotiable`
has to be readable on every product card, and a join per card to answer a
boolean is a cost paid on the busiest query in the catalogue. The vendor's floor
sits here too and is simply never named in any public schema.

A negotiation is a conversation about one product between one shopper and one
vendor, pinned to the existing chat thread rather than a second inbox. Offers are
rows, not chat text, so the state machine is enforced by the database and a
dispute can be reconstructed exactly.

An agreement is the only thing checkout trusts. It is private to one shopper,
covers one quantity, expires, and is consumed exactly once — the unique partial
index on `order_id` is what makes "consumed exactly once" true even if two
checkouts race.

Every column is additive and nullable or defaulted. Existing products stay
non-negotiable, and nothing about an existing order, cart or chat changes.

Revision ID: p9d0l18d0l18
Revises: n8c9k07c9k07
Create Date: 2026-11-20
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "p9d0l18d0l18"
down_revision = "n8c9k07c9k07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    product_columns = {c["name"] for c in inspector.get_columns("products")}
    if "is_negotiable" not in product_columns:
        op.add_column(
            "products",
            sa.Column("is_negotiable", sa.Boolean(), nullable=False, server_default="false"),
        )
    if "min_acceptable_price" not in product_columns:
        # Never serialized into a public schema. Integer to match every other
        # price column on this table.
        op.add_column("products", sa.Column("min_acceptable_price", sa.Integer(), nullable=True))
    if "negotiation_max_quantity" not in product_columns:
        op.add_column(
            "products", sa.Column("negotiation_max_quantity", sa.Integer(), nullable=True)
        )
    if "negotiation_note" not in product_columns:
        # Vendor-written conditions, shown to shoppers ("bulk orders only").
        op.add_column("products", sa.Column("negotiation_note", sa.String(length=255), nullable=True))

    if "price_negotiations" not in tables:
        op.create_table(
            "price_negotiations",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("reference", sa.String(length=20), nullable=False, unique=True),
            sa.Column(
                "product_id",
                sa.String(length=100),
                sa.ForeignKey("products.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "store_id",
                sa.String(length=50),
                sa.ForeignKey("stores.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "vendor_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "customer_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            # The existing store thread, so negotiation messages land in the
            # conversation the two parties already have.
            sa.Column(
                "thread_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("chat_threads.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("selected_color", sa.String(length=60), nullable=True),
            sa.Column("selected_size", sa.String(length=60), nullable=True),
            sa.Column("listed_unit_price", sa.Integer(), nullable=False),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="open", index=True
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        # One live negotiation per shopper per product variant. A second one
        # would let the same person run two prices for the same thing.
        op.create_index(
            "uq_negotiation_open_per_variant",
            "price_negotiations",
            ["customer_user_id", "product_id", "selected_color", "selected_size"],
            unique=True,
            postgresql_where=sa.text("status = 'open'"),
        )
        op.create_index(
            "ix_negotiations_vendor_status",
            "price_negotiations",
            ["vendor_user_id", "status"],
        )

    if "negotiation_offers" not in tables:
        op.create_table(
            "negotiation_offers",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "negotiation_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("price_negotiations.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "created_by_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            # "customer" | "vendor" — who moved, independent of which account.
            sa.Column("actor_role", sa.String(length=10), nullable=False),
            sa.Column("unit_price", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("message", sa.String(length=500), nullable=True),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="pending", index=True
            ),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        # At most one offer awaiting a reply per negotiation. This is what makes
        # a double-tapped "send offer" a no-op rather than two live offers.
        op.create_index(
            "uq_negotiation_single_pending_offer",
            "negotiation_offers",
            ["negotiation_id"],
            unique=True,
            postgresql_where=sa.text("status = 'pending'"),
        )

    if "negotiation_agreements" not in tables:
        op.create_table(
            "negotiation_agreements",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("reference", sa.String(length=20), nullable=False, unique=True),
            sa.Column(
                "negotiation_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("price_negotiations.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "offer_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("negotiation_offers.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "product_id",
                sa.String(length=100),
                sa.ForeignKey("products.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "vendor_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "customer_user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("selected_color", sa.String(length=60), nullable=True),
            sa.Column("selected_size", sa.String(length=60), nullable=True),
            # The shelf price when the deal was struck, kept so a later reprice
            # cannot rewrite what was agreed or what the saving was.
            sa.Column("listed_unit_price", sa.Integer(), nullable=False),
            sa.Column("agreed_unit_price", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="GHS"),
            sa.Column(
                "status", sa.String(length=20), nullable=False, server_default="accepted", index=True
            ),
            sa.Column("accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("order_id", sa.String(length=50), nullable=True),
            sa.Column("order_number", sa.String(length=50), nullable=True),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        # An agreement buys one order. Two concurrent checkouts both reaching
        # the update will have one of them rejected by this index rather than
        # both succeeding.
        op.create_index(
            "uq_agreement_single_order",
            "negotiation_agreements",
            ["order_id"],
            unique=True,
            postgresql_where=sa.text("order_id IS NOT NULL"),
        )
        # Checkout's hot path: this shopper's live agreement for this product.
        op.create_index(
            "ix_agreements_customer_product_status",
            "negotiation_agreements",
            ["customer_user_id", "product_id", "status"],
        )

    message_columns = {c["name"] for c in inspector.get_columns("chat_messages")}
    if "negotiation_offer_id" not in message_columns:
        # An offer card is a message that points at the offer row. Rendering it
        # from the row rather than from text means the card cannot disagree with
        # the state machine.
        op.add_column(
            "chat_messages",
            sa.Column(
                "negotiation_offer_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("negotiation_offers.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    message_columns = {c["name"] for c in inspector.get_columns("chat_messages")}
    if "negotiation_offer_id" in message_columns:
        op.drop_column("chat_messages", "negotiation_offer_id")

    for table in ("negotiation_agreements", "negotiation_offers", "price_negotiations"):
        if table in tables:
            op.drop_table(table)

    product_columns = {c["name"] for c in inspector.get_columns("products")}
    for column in (
        "negotiation_note",
        "negotiation_max_quantity",
        "min_acceptable_price",
        "is_negotiable",
    ):
        if column in product_columns:
            op.drop_column("products", column)
