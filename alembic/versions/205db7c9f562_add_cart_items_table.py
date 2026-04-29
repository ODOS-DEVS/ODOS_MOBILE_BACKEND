"""add cart items table

Revision ID: 205db7c9f562
Revises: 2f08ccf7b4a1
Create Date: 2026-04-28 14:25:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "205db7c9f562"
down_revision: Union[str, None] = "2f08ccf7b4a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cart_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("image_url", sa.String(length=500), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("price", sa.String(length=50), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "product_id", name="uq_cart_items_user_id_product_id"),
    )
    op.create_index(op.f("ix_cart_items_user_id"), "cart_items", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cart_items_user_id"), table_name="cart_items")
    op.drop_table("cart_items")
