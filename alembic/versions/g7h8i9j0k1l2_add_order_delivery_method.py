"""add order delivery method

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-24

"""

from alembic import op
import sqlalchemy as sa

revision = "g7h8i9j0k1l2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "delivery_method",
            sa.String(length=20),
            nullable=False,
            server_default="economy",
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "delivery_method")
