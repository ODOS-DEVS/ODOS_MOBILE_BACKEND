"""add return request evidence images

Revision ID: a8c4d1e7b992
Revises: f3b9d2c4a871
Create Date: 2026-05-15 14:05:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a8c4d1e7b992"
down_revision = "f3b9d2c4a871"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "return_requests",
        sa.Column(
            "evidence_image_urls",
            postgresql.ARRAY(sa.String(length=500)),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("return_requests", "evidence_image_urls")
