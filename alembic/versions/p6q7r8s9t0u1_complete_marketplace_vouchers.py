"""Complete marketplace voucher ownership and targeting fields.

Revision ID: p6q7r8s9t0u1
Revises: o5p6q7r8s9t0
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision = "p6q7r8s9t0u1"
down_revision = "o5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("vouchers")}
    indexes = {idx["name"] for idx in inspect(bind).get_indexes("vouchers")}

    if "owner_type" not in columns:
        op.add_column(
            "vouchers",
            sa.Column(
                "owner_type",
                sa.String(length=20),
                nullable=False,
                server_default="platform",
            ),
        )
    if "eligible_store_ids" not in columns:
        op.add_column(
            "vouchers",
            sa.Column(
                "eligible_store_ids",
                postgresql.ARRAY(sa.String(length=50)),
                nullable=True,
            ),
        )
    if "excluded_category_slugs" not in columns:
        op.add_column(
            "vouchers",
            sa.Column(
                "excluded_category_slugs",
                postgresql.ARRAY(sa.String(length=80)),
                nullable=True,
            ),
        )

    if "ix_vouchers_owner_type" not in indexes:
        op.create_index(op.f("ix_vouchers_owner_type"), "vouchers", ["owner_type"], unique=False)
    if "ix_vouchers_is_active" not in indexes:
        op.create_index(op.f("ix_vouchers_is_active"), "vouchers", ["is_active"], unique=False)
    if "ix_vouchers_ends_at" not in indexes:
        op.create_index(op.f("ix_vouchers_ends_at"), "vouchers", ["ends_at"], unique=False)
    if "ix_vouchers_starts_at" not in indexes:
        op.create_index(op.f("ix_vouchers_starts_at"), "vouchers", ["starts_at"], unique=False)

    # Vendor-owned store vouchers: created by a user, or still pending vendor review.
    op.execute(
        """
        UPDATE vouchers
        SET owner_type = 'vendor'
        WHERE scope = 'store'
          AND store_id IS NOT NULL
          AND (
            created_by_user_id IS NOT NULL
            OR approval_status = 'pending'
          )
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_vouchers_starts_at"), table_name="vouchers")
    op.drop_index(op.f("ix_vouchers_ends_at"), table_name="vouchers")
    op.drop_index(op.f("ix_vouchers_is_active"), table_name="vouchers")
    op.drop_index(op.f("ix_vouchers_owner_type"), table_name="vouchers")
    op.drop_column("vouchers", "excluded_category_slugs")
    op.drop_column("vouchers", "eligible_store_ids")
    op.drop_column("vouchers", "owner_type")
