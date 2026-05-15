"""add product returnability

Revision ID: ce5f6b7a8d91
Revises: a8c4d1e7b992
Create Date: 2026-05-15 12:25:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "ce5f6b7a8d91"
down_revision: Union[str, None] = "a8c4d1e7b992"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "is_returnable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "order_items",
        sa.Column(
            "is_returnable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("order_items", "is_returnable")
    op.drop_column("products", "is_returnable")
