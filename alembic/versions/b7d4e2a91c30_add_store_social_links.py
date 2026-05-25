"""add store social links

Revision ID: b7d4e2a91c30
Revises: a3f8c2d91e40
Create Date: 2026-05-25 18:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7d4e2a91c30"
down_revision: Union[str, Sequence[str], None] = "a3f8c2d91e40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("stores", "vendor_applications"):
        prefix = "store_" if table == "vendor_applications" else ""
        op.add_column(
            table,
            sa.Column(f"{prefix}instagram_url", sa.String(length=255), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(f"{prefix}facebook_url", sa.String(length=255), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(f"{prefix}tiktok_url", sa.String(length=255), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(f"{prefix}twitter_url", sa.String(length=255), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(f"{prefix}whatsapp_url", sa.String(length=255), nullable=True),
        )
        op.add_column(
            table,
            sa.Column(f"{prefix}website_url", sa.String(length=255), nullable=True),
        )


def downgrade() -> None:
    for table in ("stores", "vendor_applications"):
        prefix = "store_" if table == "vendor_applications" else ""
        op.drop_column(table, f"{prefix}website_url")
        op.drop_column(table, f"{prefix}whatsapp_url")
        op.drop_column(table, f"{prefix}twitter_url")
        op.drop_column(table, f"{prefix}tiktok_url")
        op.drop_column(table, f"{prefix}facebook_url")
        op.drop_column(table, f"{prefix}instagram_url")
