"""drop unused profile fields

Revision ID: 0d6f0d0a7c43
Revises: 4d08b7cfb5b2
Create Date: 2026-04-25 14:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0d6f0d0a7c43"
down_revision = "4d08b7cfb5b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "bio")
    op.drop_column("users", "country")


def downgrade() -> None:
    op.add_column("users", sa.Column("country", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("bio", sa.String(length=500), nullable=True))
