"""Let shoppers save stores, and let shelves carry their own copy and artwork.

Two additions that belong together because both make a store page a destination
rather than a product list.

`saved_stores` is a plain join with a unique constraint: saving a store twice is
a no-op rather than a second row that would show the shop twice on the saved
page. It is scoped to a user, not a session, so a save survives logout and
follows the shopper to another device — the web had only a localStorage list,
which did neither.

Sections gain a description and a cover image. They already had a title and an
order; what they lacked was anything to say what a shelf is for, which is what
makes a section page worth landing on.

Both changes are additive and nullable. Existing sections keep working with no
description, and no existing row is rewritten.

Revision ID: n8c9k07c9k07
Revises: m7b8j95b7j95
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "n8c9k07c9k07"
down_revision = "m7b8j95b7j95"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    section_columns = {c["name"] for c in inspector.get_columns("store_sections")}
    if "description" not in section_columns:
        op.add_column(
            "store_sections",
            sa.Column("description", sa.String(length=500), nullable=True),
        )
    if "cover_image_url" not in section_columns:
        op.add_column(
            "store_sections",
            sa.Column("cover_image_url", sa.String(length=500), nullable=True),
        )

    if "saved_stores" not in set(inspector.get_table_names()):
        op.create_table(
            "saved_stores",
            sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "user_id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "store_id",
                sa.String(length=50),
                sa.ForeignKey("stores.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            # Saving the same shop twice is the same save. The constraint is what
            # makes the API's "save" idempotent rather than duplicating the row.
            sa.UniqueConstraint("user_id", "store_id", name="uq_saved_stores_user_store"),
        )
        # The saved-stores page reads newest-first for one user on every load.
        op.create_index(
            "ix_saved_stores_user_created",
            "saved_stores",
            ["user_id", "created_at"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    if "saved_stores" in set(inspector.get_table_names()):
        indexes = {index["name"] for index in inspector.get_indexes("saved_stores")}
        if "ix_saved_stores_user_created" in indexes:
            op.drop_index("ix_saved_stores_user_created", table_name="saved_stores")
        op.drop_table("saved_stores")

    section_columns = {c["name"] for c in inspector.get_columns("store_sections")}
    for column in ("cover_image_url", "description"):
        if column in section_columns:
            op.drop_column("store_sections", column)
