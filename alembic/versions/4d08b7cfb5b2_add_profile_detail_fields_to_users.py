"""add profile detail fields to users

Revision ID: 4d08b7cfb5b2
Revises: ac6a722ceaab
Create Date: 2026-04-24 10:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4d08b7cfb5b2"
down_revision: Union[str, None] = "ac6a722ceaab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("gender", sa.String(length=30), nullable=True))
    op.add_column("users", sa.Column("city", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("region", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("country", sa.String(length=120), nullable=True))
    op.add_column("users", sa.Column("bio", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "bio")
    op.drop_column("users", "country")
    op.drop_column("users", "region")
    op.drop_column("users", "city")
    op.drop_column("users", "gender")
