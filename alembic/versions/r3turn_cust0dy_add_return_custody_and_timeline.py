"""Add return custody fields and the return status timeline

Revision ID: r3turncust0dy
Revises: n0d0s1r3c0n
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "r3turncust0dy"
down_revision = "n0d0s1r3c0n"
branch_labels = None
depends_on = None


# Same guard style as n0d0s1r3c0n, which has already run against this
# project's production database. Reusing a proven pattern rather than a new
# one built on raw information_schema queries.
def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _has_column(table: str, column: str) -> bool:
    if table not in _tables():
        return False
    return column in {col["name"] for col in _inspector().get_columns(table)}


def _has_table(table: str) -> bool:
    return table in _tables()


def upgrade() -> None:
    # Guarded like the other reconcile migrations in this project: production
    # has been repaired by hand before, so a column existing already is a
    # normal state rather than an error.
    if not _has_column("return_requests", "collected_at"):
        op.add_column(
            "return_requests",
            sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column("return_requests", "received_at"):
        op.add_column(
            "return_requests",
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column("return_requests", "received_by_user_id"):
        op.add_column(
            "return_requests",
            sa.Column("received_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_return_requests_received_by_user_id_users",
            "return_requests",
            "users",
            ["received_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(
            "ix_return_requests_received_by_user_id",
            "return_requests",
            ["received_by_user_id"],
        )
    if not _has_column("return_requests", "received_condition_note"):
        op.add_column(
            "return_requests",
            sa.Column("received_condition_note", sa.String(length=500), nullable=True),
        )
    if not _has_column("return_requests", "return_waived"):
        op.add_column(
            "return_requests",
            sa.Column(
                "return_waived",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )

    if not _has_table("return_status_events"):
        op.create_table(
            "return_status_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "return_request_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("return_requests.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("actor_role", sa.String(length=20), nullable=False),
            sa.Column(
                "actor_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("note", sa.String(length=1000), nullable=True),
            sa.Column("refund_amount", sa.Float(), nullable=True),
            sa.Column("event_metadata", postgresql.JSONB(), nullable=True),
            sa.Column(
                "occurred_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_return_status_events_return_request_id",
            "return_status_events",
            ["return_request_id"],
        )
        op.create_index(
            "ix_return_status_events_occurred_at",
            "return_status_events",
            ["occurred_at"],
        )

    # Seed a timeline entry for every existing request so history does not start
    # empty for returns that are already open. Uses the request's own timestamps
    # rather than inventing them.
    op.execute(
        sa.text(
            """
            INSERT INTO return_status_events
                (id, return_request_id, status, actor_role, actor_id, note, refund_amount, occurred_at)
            SELECT gen_random_uuid(), r.id, r.status, 'system', NULL,
                   'Backfilled from the request record', r.refund_amount,
                   COALESCE(r.reviewed_at, r.created_at)
            FROM return_requests r
            WHERE NOT EXISTS (
                SELECT 1 FROM return_status_events e WHERE e.return_request_id = r.id
            )
            """
        )
    )


def downgrade() -> None:
    # Intentionally not implemented: dropping the timeline would destroy the
    # audit trail these columns exist to provide.
    pass
