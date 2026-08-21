"""Upgrade vouchers into a rule-based promotion engine."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "k1l2m3n4o5p6"
down_revision = "j0k1l2m3n4o5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vouchers",
        sa.Column("promotion_type", sa.String(length=30), nullable=False, server_default="coupon"),
    )
    op.add_column(
        "vouchers",
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "vouchers",
        sa.Column("stackable", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "vouchers",
        sa.Column("exclusive_group", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("auto_apply", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "vouchers",
        sa.Column("bogo_buy_quantity", sa.Integer(), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("bogo_get_quantity", sa.Integer(), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("bogo_get_discount_percent", sa.Float(), nullable=True),
    )
    op.add_column(
        "vouchers",
        sa.Column("rules_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index(op.f("ix_vouchers_promotion_type"), "vouchers", ["promotion_type"], unique=False)
    op.create_index(op.f("ix_vouchers_auto_apply"), "vouchers", ["auto_apply"], unique=False)
    op.create_index(op.f("ix_vouchers_priority"), "vouchers", ["priority"], unique=False)

    op.add_column(
        "orders",
        sa.Column("promotion_breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.execute(
        """
        UPDATE vouchers
        SET promotion_type = CASE
            WHEN discount_type = 'free_shipping' THEN 'free_shipping'
            WHEN scope IN ('product', 'category') THEN 'product'
            ELSE 'coupon'
        END
        WHERE promotion_type = 'coupon'
        """
    )


def downgrade() -> None:
    op.drop_column("orders", "promotion_breakdown")
    op.drop_index(op.f("ix_vouchers_priority"), table_name="vouchers")
    op.drop_index(op.f("ix_vouchers_auto_apply"), table_name="vouchers")
    op.drop_index(op.f("ix_vouchers_promotion_type"), table_name="vouchers")
    op.drop_column("vouchers", "rules_json")
    op.drop_column("vouchers", "bogo_get_discount_percent")
    op.drop_column("vouchers", "bogo_get_quantity")
    op.drop_column("vouchers", "bogo_buy_quantity")
    op.drop_column("vouchers", "auto_apply")
    op.drop_column("vouchers", "exclusive_group")
    op.drop_column("vouchers", "stackable")
    op.drop_column("vouchers", "priority")
    op.drop_column("vouchers", "promotion_type")
