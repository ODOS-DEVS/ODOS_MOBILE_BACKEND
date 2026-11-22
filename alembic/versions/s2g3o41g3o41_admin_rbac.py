"""Administrator roles, permissions, overrides and invitations.

Schema only. Seeding the permission catalogue, creating the default roles and
giving existing administrators the role matching their old band all happen in
`app.services.admin_rbac_bootstrap`, which is idempotent and runs on startup.

Keeping the data out of here is deliberate: a migration is a fixed point in
history, and the permission catalogue is not — baking today's 96 permissions
into it would mean this file did something different the day someone adds the
97th. The DDL below is the part that genuinely belongs to this revision.

Nothing existing is altered. `users.admin_permission` is untouched, so every
current administrator — including the Super Admin — keeps working exactly as
before whether or not the bootstrap has run.

Revision ID: s2g3o41g3o41
Revises: r1f2n30f2n30
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "s2g3o41g3o41"
down_revision = "r1f2n30f2n30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_permissions",
        sa.Column("code", sa.String(length=80), primary_key=True),
        sa.Column("module", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_retired", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_admin_permissions_module", "admin_permissions", ["module"])

    op.create_table(
        "admin_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_admin_roles_slug"),
    )
    op.create_index("ix_admin_roles_active", "admin_roles", ["is_active"])

    op.create_table(
        "admin_role_permissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission_code",
            sa.String(length=80),
            sa.ForeignKey("admin_permissions.code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("role_id", "permission_code", name="uq_admin_role_permission"),
    )
    op.create_index("ix_admin_role_permissions_code", "admin_role_permissions", ["permission_code"])

    op.create_table(
        "admin_user_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "role_id", name="uq_admin_user_role"),
    )
    op.create_index("ix_admin_user_roles_user", "admin_user_roles", ["user_id"])

    op.create_table(
        "admin_permission_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission_code",
            sa.String(length=80),
            sa.ForeignKey("admin_permissions.code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("effect", sa.String(length=10), nullable=False, server_default="grant"),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "permission_code", name="uq_admin_permission_override"),
    )
    op.create_index(
        "ix_admin_permission_overrides_user", "admin_permission_overrides", ["user_id"]
    )

    op.create_table(
        "admin_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        # Only the hash. The raw token lives in the invitation email and nowhere else.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "accepted_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "invited_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("send_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_admin_invitations_token_hash"),
    )
    op.create_index("ix_admin_invitations_email", "admin_invitations", ["email"])
    op.create_index(
        "ix_admin_invitations_email_status", "admin_invitations", ["email", "status"]
    )
    op.create_index(
        "ix_admin_invitations_status_expires", "admin_invitations", ["status", "expires_at"]
    )
    # One live invitation per address. A partial index rather than a plain
    # unique constraint so a revoked or accepted invitation does not block
    # inviting the same person again later.
    op.create_index(
        "uq_admin_invitations_one_pending_per_email",
        "admin_invitations",
        ["email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "admin_invitation_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invitation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_invitations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("invitation_id", "role_id", name="uq_admin_invitation_role"),
    )


def downgrade() -> None:
    op.drop_table("admin_invitation_roles")
    op.drop_index("uq_admin_invitations_one_pending_per_email", table_name="admin_invitations")
    op.drop_index("ix_admin_invitations_status_expires", table_name="admin_invitations")
    op.drop_index("ix_admin_invitations_email_status", table_name="admin_invitations")
    op.drop_index("ix_admin_invitations_email", table_name="admin_invitations")
    op.drop_table("admin_invitations")
    op.drop_index(
        "ix_admin_permission_overrides_user", table_name="admin_permission_overrides"
    )
    op.drop_table("admin_permission_overrides")
    op.drop_index("ix_admin_user_roles_user", table_name="admin_user_roles")
    op.drop_table("admin_user_roles")
    op.drop_index("ix_admin_role_permissions_code", table_name="admin_role_permissions")
    op.drop_table("admin_role_permissions")
    op.drop_index("ix_admin_roles_active", table_name="admin_roles")
    op.drop_table("admin_roles")
    op.drop_index("ix_admin_permissions_module", table_name="admin_permissions")
    op.drop_table("admin_permissions")
