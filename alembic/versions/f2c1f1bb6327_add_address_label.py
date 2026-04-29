"""add address label

Revision ID: f2c1f1bb6327
Revises: 17b4d0b9f1a2
Create Date: 2026-04-29 19:20:00.000000
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f2c1f1bb6327"
down_revision: str | None = "17b4d0b9f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("saved_addresses", sa.Column("label", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("saved_addresses", "label")
