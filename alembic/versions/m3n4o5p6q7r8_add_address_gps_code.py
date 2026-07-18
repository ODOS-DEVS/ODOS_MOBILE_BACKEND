"""Add optional GhanaPost GPS code to saved addresses."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "m3n4o5p6q7r8"
down_revision = "l2m3n4o5p6q7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("saved_addresses")}
    if "gps_code" in columns:
        return
    op.add_column(
        "saved_addresses",
        sa.Column("gps_code", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("saved_addresses")}
    if "gps_code" not in columns:
        return
    op.drop_column("saved_addresses", "gps_code")
