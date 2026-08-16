"""Add attachment columns to chat_messages for images, files, and voice
notes sent in customer/vendor and support chat threads.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "f81f085cfcda"
down_revision = "z6a7b8c9d0e1"
branch_labels = None
depends_on = None

NEW_COLUMNS = [
    ("attachment_url", sa.String(length=1000)),
    ("attachment_type", sa.String(length=20)),
    ("attachment_name", sa.String(length=255)),
    ("attachment_duration_seconds", sa.Integer()),
]


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in inspect(bind).get_columns("chat_messages")}
    for name, col_type in NEW_COLUMNS:
        if name not in existing_columns:
            op.add_column("chat_messages", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = {col["name"] for col in inspect(bind).get_columns("chat_messages")}
    for name, _col_type in reversed(NEW_COLUMNS):
        if name in existing_columns:
            op.drop_column("chat_messages", name)
