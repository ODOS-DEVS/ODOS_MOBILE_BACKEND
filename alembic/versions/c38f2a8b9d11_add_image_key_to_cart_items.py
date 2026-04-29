"""add image key to cart items

Revision ID: c38f2a8b9d11
Revises: a51d7299e7f4
Create Date: 2026-04-29 19:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c38f2a8b9d11"
down_revision: Union[str, Sequence[str], None] = "a51d7299e7f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cart_items", sa.Column("image_key", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("cart_items", "image_key")
