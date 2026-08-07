"""Add delivery experience fields: instructions, rating, reschedule request,
dispatch photo, and departure notification tracking."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "w3x4y5z6a7b8"
down_revision = "v2w3x4y5z6a7"
branch_labels = None
depends_on = None

NEW_COLUMNS = [
    ("delivery_instructions", sa.String(length=280)),
    ("delivery_rating", sa.Integer()),
    ("delivery_rated_at", sa.DateTime(timezone=True)),
    ("reschedule_requested_at", sa.DateTime(timezone=True)),
    ("reschedule_note", sa.String(length=280)),
    ("dispatch_photo_url", sa.String(length=500)),
    ("dispatch_photo_key", sa.String(length=100)),
    ("departure_notified_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("orders")}
    for name, col_type in NEW_COLUMNS:
        if name not in columns:
            op.add_column("orders", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = {col["name"] for col in inspect(bind).get_columns("orders")}
    for name, _col_type in reversed(NEW_COLUMNS):
        if name in columns:
            op.drop_column("orders", name)
