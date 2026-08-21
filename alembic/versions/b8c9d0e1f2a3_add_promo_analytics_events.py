"""Create promo_analytics_events table for tracking impressions, clicks, and
conversions for campaigns, vouchers, and promo banners."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b8c9d0e1f2a3"
down_revision = "aaabbbcccddd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "promo_analytics_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("entity_type", sa.String(20), nullable=False, index=True),
        sa.Column("entity_id", sa.String(64), nullable=False, index=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("source_screen", sa.String(80), nullable=True),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            index=True,
        ),
        sa.Column("discount_amount", sa.Float(), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            index=True,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
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
        "ix_promo_analytics_user_created",
        "promo_analytics_events",
        ["user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_promo_analytics_user_created", table_name="promo_analytics_events")
    op.drop_index(
        "ix_promo_analytics_entity_event_created", table_name="promo_analytics_events"
    )
    op.drop_index(
        "ix_promo_analytics_entity_created", table_name="promo_analytics_events"
    )
    op.drop_table("promo_analytics_events")
