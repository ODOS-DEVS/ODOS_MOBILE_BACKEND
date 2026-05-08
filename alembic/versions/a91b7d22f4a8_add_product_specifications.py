"""add product specifications

Revision ID: a91b7d22f4a8
Revises: e7a2b4c9d1f0
Create Date: 2026-05-06 10:45:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a91b7d22f4a8"
down_revision = "e7a2b4c9d1f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "specifications",
            postgresql.ARRAY(sa.String(length=255)),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("products", "specifications")
