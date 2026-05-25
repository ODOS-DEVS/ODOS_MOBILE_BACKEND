"""add store geo coordinates

Revision ID: a3f8c2d91e40
Revises: 2f4c6a1d9e32
Create Date: 2026-05-25 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3f8c2d91e40"
down_revision: Union[str, Sequence[str], None] = "2f4c6a1d9e32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("stores", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("stores", sa.Column("longitude", sa.Float(), nullable=True))
    op.add_column(
        "vendor_applications",
        sa.Column("store_latitude", sa.Float(), nullable=True),
    )
    op.add_column(
        "vendor_applications",
        sa.Column("store_longitude", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vendor_applications", "store_longitude")
    op.drop_column("vendor_applications", "store_latitude")
    op.drop_column("stores", "longitude")
    op.drop_column("stores", "latitude")
