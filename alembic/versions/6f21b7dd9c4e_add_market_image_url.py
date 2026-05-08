"""add market image url

Revision ID: 6f21b7dd9c4e
Revises: c4d8e1b7a2f0
Create Date: 2026-05-07 18:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6f21b7dd9c4e"
down_revision = "c4d8e1b7a2f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("markets", sa.Column("image_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("markets", "image_url")
