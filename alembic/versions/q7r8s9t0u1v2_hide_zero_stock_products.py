"""Hide existing zero-stock active products from the shopper catalog.

Revision ID: q7r8s9t0u1v2
Revises: p6q7r8s9t0u1
Create Date: 2026-07-17
"""

from alembic import op

revision = "q7r8s9t0u1v2"
down_revision = "p6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE products
        SET status = 'out_of_stock',
            is_active = false
        WHERE stock <= 0
          AND status = 'active'
          AND is_active = true
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE products
        SET status = 'active',
            is_active = true
        WHERE status = 'out_of_stock'
        """
    )
