"""scope vouchers and add assignments

Revision ID: d2a7f91ce441
Revises: b61f0d92c3ae
Create Date: 2026-05-12 10:40:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "d2a7f91ce441"
down_revision = "b61f0d92c3ae"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vouchers",
        sa.Column("scope", sa.String(length=20), server_default="odos", nullable=False),
    )
    op.add_column(
        "vouchers",
        sa.Column("availability", sa.String(length=20), server_default="auto", nullable=False),
    )
    op.add_column(
        "vouchers",
        sa.Column("store_id", sa.String(length=50), nullable=True),
    )
    op.create_index(op.f("ix_vouchers_scope"), "vouchers", ["scope"], unique=False)
    op.create_index(op.f("ix_vouchers_availability"), "vouchers", ["availability"], unique=False)
    op.create_index(op.f("ix_vouchers_store_id"), "vouchers", ["store_id"], unique=False)
    op.create_foreign_key(
        "fk_vouchers_store_id_stores",
        "vouchers",
        "stores",
        ["store_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_table(
        "voucher_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voucher_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=20), server_default="claim", nullable=False),
        sa.Column("assigned_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voucher_id"], ["vouchers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("voucher_id", "user_id", name="uq_voucher_assignments_voucher_user"),
    )
    op.create_index(
        op.f("ix_voucher_assignments_voucher_id"),
        "voucher_assignments",
        ["voucher_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voucher_assignments_user_id"),
        "voucher_assignments",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voucher_assignments_assigned_by_user_id"),
        "voucher_assignments",
        ["assigned_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_voucher_assignments_assigned_by_user_id"),
        table_name="voucher_assignments",
    )
    op.drop_index(op.f("ix_voucher_assignments_user_id"), table_name="voucher_assignments")
    op.drop_index(op.f("ix_voucher_assignments_voucher_id"), table_name="voucher_assignments")
    op.drop_table("voucher_assignments")

    op.drop_constraint("fk_vouchers_store_id_stores", "vouchers", type_="foreignkey")
    op.drop_index(op.f("ix_vouchers_store_id"), table_name="vouchers")
    op.drop_index(op.f("ix_vouchers_availability"), table_name="vouchers")
    op.drop_index(op.f("ix_vouchers_scope"), table_name="vouchers")
    op.drop_column("vouchers", "store_id")
    op.drop_column("vouchers", "availability")
    op.drop_column("vouchers", "scope")
