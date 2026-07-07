"""Add optional GhanaPost GPS code to saved addresses."""

from alembic import op
import sqlalchemy as sa

revision = "m3n4o5p6q7r8"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "saved_addresses",
        sa.Column("gps_code", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("saved_addresses", "gps_code")
