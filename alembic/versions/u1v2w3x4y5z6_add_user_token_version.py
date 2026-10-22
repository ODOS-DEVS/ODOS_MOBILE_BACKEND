"""Add users.token_version for session revocation on password change.

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa
from app.core.migration_guards import (
    add_column_if_missing,
    create_index_if_missing,
    create_table_if_missing,
)

revision = "u1v2w3x4y5z6"
down_revision = "t0u1v2w3x4y5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    add_column_if_missing(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
