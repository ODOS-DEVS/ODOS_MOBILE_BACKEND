"""Add guide_videos: tutorial videos attached to resource-center guides.

The written guides live in the mobile bundle; this table holds only the
video for each one, so `guide_slug` is an indexed plain string rather than a
foreign key. Creating the table is enough — a guide with no row here renders
no video section at all, which is the intended empty state.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "g1v2d30v1d30"
down_revision = "acc7d3l3t10n"
branch_labels = None
depends_on = None

TABLE_NAME = "guide_videos"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME in inspector.get_table_names():
        return

    op.create_table(
        TABLE_NAME,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("guide_slug", sa.String(length=80), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=140), nullable=False),
        sa.Column("description", sa.String(length=400), nullable=True),
        sa.Column("video_url", sa.String(length=500), nullable=False),
        sa.Column("thumbnail_url", sa.String(length=500), nullable=True),
        sa.Column(
            "provider",
            sa.String(length=40),
            server_default="cloudinary",
            nullable=False,
        ),
        sa.Column("public_id", sa.String(length=300), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("is_published", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index("ix_guide_videos_guide_slug", TABLE_NAME, ["guide_slug"])
    op.create_index(
        "ix_guide_videos_published_slug",
        TABLE_NAME,
        ["is_published", "guide_slug"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE_NAME not in inspector.get_table_names():
        return

    op.drop_index("ix_guide_videos_published_slug", table_name=TABLE_NAME)
    op.drop_index("ix_guide_videos_guide_slug", table_name=TABLE_NAME)
    op.drop_table(TABLE_NAME)
