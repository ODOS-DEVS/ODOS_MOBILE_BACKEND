"""allow half-star review ratings

Revision ID: ab12d4f6e8c9
Revises: 9f31b2d4a6c8
Create Date: 2026-05-11 22:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ab12d4f6e8c9"
down_revision = "9f31b2d4a6c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "reviews",
        "rating",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        postgresql_using="rating::double precision",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "reviews",
        "rating",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        postgresql_using="ROUND(rating)::integer",
        existing_nullable=False,
    )
