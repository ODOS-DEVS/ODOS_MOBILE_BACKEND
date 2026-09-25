"""Add users.deleted_at for account deletion.

Apple requires an app that creates accounts to let people delete theirs from
inside the app (Guideline 5.1.1(v)). A hard delete is not an option here: the
User -> orders relationship cascades, so removing a row would take the order
history with it, and those rows are a vendor's sales record and the platform's
financial record. They belong to more parties than the person leaving.

So deletion is a soft delete plus anonymisation, and this column is what marks
it. is_active already gates login and token validation, so it does the locking
out; deleted_at records that the deactivation was the user's own request rather
than an admin suspension, which is a distinction worth keeping.

Revision ID: acc7d3l3t10n
Revises: pkg1sp1itd3l
"""

import sqlalchemy as sa
from alembic import op

revision = "acc7d3l3t10n"
down_revision = "pkg1sp1itd3l"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    # Deleted accounts are excluded from nearly every query, so the index earns
    # its place on the "is this row still live" check rather than on lookups of
    # deleted rows, which are rare.
    op.create_index("ix_users_deleted_at", "users", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_users_deleted_at", table_name="users")
    op.drop_column("users", "deleted_at")
