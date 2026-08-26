"""Reconcile promo/loyalty schema against the models, whatever the stamp says

Databases in this project have been stamped forward past migrations that never
actually ran (see app/core/alembic_recovery.py, which used to jump the stamp
over un-run revisions whenever it recognised a later table). The result is a
database that alembic believes is at head while `vouchers.eligibility_rules`,
`merchandising_campaigns.eligibility_rules` and the whole loyalty /
promo-analytics table set are absent — which 500s the public catalog, the
promotions feed and, silently, vendor wallet settlement.

This revision re-checks every object those skipped migrations were supposed to
create and adds whatever is still missing. Every statement is guarded, so it is
a no-op on a database that is genuinely up to date and a repair on one that is
not. It is safe to run repeatedly.

Revision ID: n0d0s1r3c0n
Revises: ce8d9f0a1b2c
Create Date: 2026-08-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "n0d0s1r3c0n"
down_revision: Union[str, Sequence[str], None] = "ce8d9f0a1b2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    return {col["name"] for col in _inspector().get_columns(table)}


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    if table not in _tables():
        return
    if column.name not in _columns(table):
        op.add_column(table, column)


def upgrade() -> None:
    existing = _tables()

    # --- columns skipped by bd7c8e9f0a1b ---------------------------------
    # Absent, these break /api/catalog/campaigns, /api/catalog/deals-hub,
    # /api/vouchers/promotions and vendor wallet settlement on delivery.
    _add_column_if_missing("vouchers", sa.Column("eligibility_rules", sa.JSON(), nullable=True))
    _add_column_if_missing(
        "merchandising_campaigns", sa.Column("eligibility_rules", sa.JSON(), nullable=True)
    )

    # --- tables skipped by a8c0d1e2f3a4 -----------------------------------
    if "flash_sale_events" not in existing:
        op.create_table(
            "flash_sale_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("slug", sa.String(length=80), nullable=False, unique=True),
            sa.Column("title", sa.String(length=120), nullable=False),
            sa.Column("subtitle", sa.String(length=255), nullable=True),
            sa.Column("image_url", sa.String(length=500), nullable=True),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index("ix_flash_sale_events_slug", "flash_sale_events", ["slug"], unique=True)

    if "flash_sale_event_products" not in existing:
        # Created at its FINAL shape: d4e5f6a7b8c9 (flash pricing) and
        # x4y5z6a7b8c9 (scarcity) are stamped applied on these databases and
        # will not run again, so their columns have to be included here.
        op.create_table(
            "flash_sale_event_products",
            sa.Column(
                "event_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("flash_sale_events.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "product_id",
                sa.String(length=100),
                sa.ForeignKey("products.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        )
    _add_column_if_missing(
        "flash_sale_event_products", sa.Column("flash_sale_price", sa.Float(), nullable=True)
    )
    _add_column_if_missing(
        "flash_sale_event_products", sa.Column("flash_sale_old_price", sa.Float(), nullable=True)
    )
    _add_column_if_missing(
        "flash_sale_event_products", sa.Column("stock_limit", sa.Integer(), nullable=True)
    )
    _add_column_if_missing(
        "flash_sale_event_products",
        sa.Column("units_sold", sa.Integer(), server_default="0", nullable=False),
    )

    if "loyalty_accounts" not in existing:
        op.create_table(
            "loyalty_accounts",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("total_points", sa.Integer(), server_default="0", nullable=False),
            sa.Column("tier_level", sa.String(length=20), server_default="bronze", nullable=False),
            sa.Column(
                "tier_progress_percent", sa.Float(), server_default="0.0", nullable=False
            ),
            sa.Column("lifetime_spend", sa.Float(), server_default="0.0", nullable=False),
            sa.Column("tier_upgraded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index("ix_loyalty_accounts_user_id", "loyalty_accounts", ["user_id"])
        op.create_index("idx_loyalty_tier_level", "loyalty_accounts", ["tier_level"])

    if "loyalty_transactions" not in existing:
        op.create_table(
            "loyalty_transactions",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "account_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("loyalty_accounts.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("transaction_type", sa.String(length=30), nullable=False),
            sa.Column("points_amount", sa.Integer(), nullable=False),
            sa.Column("reason", sa.String(length=255), nullable=False),
            sa.Column(
                "order_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orders.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index("ix_loyalty_transactions_account_id", "loyalty_transactions", ["account_id"])
        op.create_index("ix_loyalty_transactions_order_id", "loyalty_transactions", ["order_id"])
        op.create_index("ix_loyalty_transactions_created_at", "loyalty_transactions", ["created_at"])
        op.create_index(
            "idx_loyalty_type_created",
            "loyalty_transactions",
            ["transaction_type", "created_at"],
        )

    if "loyalty_tier_benefits" not in existing:
        op.create_table(
            "loyalty_tier_benefits",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tier_name", sa.String(length=30), nullable=False, unique=True),
            sa.Column("tier_order", sa.Integer(), server_default="0", nullable=False),
            sa.Column("min_spend", sa.Float(), nullable=False),
            sa.Column("discount_percent", sa.Float(), server_default="0.0", nullable=False),
            sa.Column("points_multiplier", sa.Float(), server_default="1.0", nullable=False),
            sa.Column("free_shipping_threshold", sa.Float(), nullable=True),
            sa.Column("birthday_bonus_points", sa.Integer(), server_default="0", nullable=False),
            sa.Column(
                "exclusive_deals_access", sa.Boolean(), server_default="false", nullable=False
            ),
            sa.Column("priority_support", sa.Boolean(), server_default="false", nullable=False),
            sa.Column("description", sa.String(length=500), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    if "promo_analytics_events" not in existing:
        op.create_table(
            "promo_analytics_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("entity_type", sa.String(length=20), nullable=False),
            sa.Column("entity_id", sa.String(length=64), nullable=False),
            sa.Column("event_type", sa.String(length=20), nullable=False),
            sa.Column(
                "user_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("session_id", sa.String(length=64), nullable=True),
            sa.Column("source_screen", sa.String(length=80), nullable=True),
            sa.Column(
                "order_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orders.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("discount_amount", sa.Float(), nullable=True),
            sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_promo_analytics_events_entity_type", "promo_analytics_events", ["entity_type"]
        )
        op.create_index(
            "ix_promo_analytics_events_entity_id", "promo_analytics_events", ["entity_id"]
        )
        op.create_index("ix_promo_analytics_events_user_id", "promo_analytics_events", ["user_id"])
        op.create_index("ix_promo_analytics_events_order_id", "promo_analytics_events", ["order_id"])
        op.create_index(
            "ix_promo_analytics_events_created_at", "promo_analytics_events", ["created_at"]
        )
        op.create_index(
            "ix_promo_analytics_entity_created",
            "promo_analytics_events",
            ["entity_type", "entity_id", "created_at"],
        )
        op.create_index(
            "ix_promo_analytics_entity_event_created",
            "promo_analytics_events",
            ["entity_type", "entity_id", "event_type", "created_at"],
        )
        op.create_index(
            "ix_promo_analytics_user_created", "promo_analytics_events", ["user_id", "created_at"]
        )


def downgrade() -> None:
    # Intentionally a no-op: this revision only ever *adds* objects that an
    # earlier revision already claims to own. Dropping them here would delete
    # data that bd7c8e9f0a1b / a8c0d1e2f3a4 are responsible for.
    pass
