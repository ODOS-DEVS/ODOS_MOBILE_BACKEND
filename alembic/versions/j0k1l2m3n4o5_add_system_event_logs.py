"""Add system event logs and admin permission bands."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "j0k1l2m3n4o5"
down_revision = "i9j0k1l2m3n4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("admin_permission", sa.String(length=30), nullable=True),
    )
    op.create_index(
        op.f("ix_users_admin_permission"),
        "users",
        ["admin_permission"],
        unique=False,
    )

    op.create_table(
        "system_event_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=True),
        sa.Column("entity_id", sa.String(length=120), nullable=True),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_system_event_logs_created_at",
        "system_event_logs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_system_event_logs_event_type_created",
        "system_event_logs",
        ["event_type", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_system_event_logs_actor_created",
        "system_event_logs",
        ["actor_type", "actor_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_system_event_logs_entity_created",
        "system_event_logs",
        ["entity_type", "entity_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_system_event_logs_action_created",
        "system_event_logs",
        ["action", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_system_event_logs_actor_id"),
        "system_event_logs",
        ["actor_id"],
        unique=False,
    )

    op.execute(
        "UPDATE users SET admin_permission = 'admin' "
        "WHERE role = 'admin' AND admin_permission IS NULL"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_system_event_logs_actor_id"), table_name="system_event_logs")
    op.drop_index("ix_system_event_logs_action_created", table_name="system_event_logs")
    op.drop_index("ix_system_event_logs_entity_created", table_name="system_event_logs")
    op.drop_index("ix_system_event_logs_actor_created", table_name="system_event_logs")
    op.drop_index("ix_system_event_logs_event_type_created", table_name="system_event_logs")
    op.drop_index("ix_system_event_logs_created_at", table_name="system_event_logs")
    op.drop_table("system_event_logs")
    op.drop_index(op.f("ix_users_admin_permission"), table_name="users")
    op.drop_column("users", "admin_permission")
