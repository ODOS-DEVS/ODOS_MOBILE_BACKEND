"""Role-based access control for administrators.

The shape of the thing
----------------------
An administrator's effective permissions are the union of the permissions on
every role they hold, with per-administrator overrides applied on top. An
explicit `deny` override always beats a grant from any role — see
`app.services.admin_rbac` for the resolution rule and why it is that way round.

Roles are rows rather than code because the Super Admin has to be able to add
and reshape them. Permissions are the opposite: `admin_permissions` mirrors the
code catalogue in `app.core.admin_permission_catalogue`, synced idempotently, so
role rows can carry a real foreign key while the catalogue stays something only
a deploy can change.

Super Admin is deliberately NOT a role here. It stays the
`users.admin_permission == "super_admin"` flag it already was, so the existing
account keeps working untouched and no role edit can ever revoke it.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

#: Override effects. Stored as text rather than a native enum so adding a third
#: effect later is a code change, not a migration against a live type.
OVERRIDE_GRANT = "grant"
OVERRIDE_DENY = "deny"

#: Invitation lifecycle.
INVITE_PENDING = "pending"
INVITE_ACCEPTED = "accepted"
INVITE_REVOKED = "revoked"
INVITE_EXPIRED = "expired"


class AdminPermission(Base):
    """A mirror of one entry in the code catalogue.

    Exists so `admin_role_permissions` and `admin_permission_overrides` can
    reference a permission by foreign key. Rows are written by the seeder, never
    by a request: creating a permission here that no code checks would grant
    nothing and mislead whoever ticked it.
    """

    __tablename__ = "admin_permissions"

    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    module: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Set when the code catalogue no longer lists this code. Kept rather than
    #: deleted so an audit row referencing it still resolves, and so a role that
    #: held it can be reported on instead of silently losing a tick.
    is_retired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AdminRole(Base):
    """A named bundle of permissions, editable by the Super Admin."""

    __tablename__ = "admin_roles"
    __table_args__ = (
        Index("ix_admin_roles_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: Stable identifier the seeder matches on, so re-seeding updates a role
    #: rather than creating a second one with the same name.
    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: Seeded roles. They can be edited and deactivated but never deleted, so a
    #: platform always has something safe to assign.
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    permissions: Mapped[list[AdminRolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )
    assignments: Mapped[list[AdminUserRole]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )


class AdminRolePermission(Base):
    """One permission granted by one role."""

    __tablename__ = "admin_role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_code", name="uq_admin_role_permission"),
        Index("ix_admin_role_permissions_code", "permission_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_roles.id", ondelete="CASCADE"), nullable=False
    )
    permission_code: Mapped[str] = mapped_column(
        String(80), ForeignKey("admin_permissions.code", ondelete="CASCADE"), nullable=False
    )

    role: Mapped[AdminRole] = relationship(back_populates="permissions")


class AdminUserRole(Base):
    """A role held by an administrator.

    Many-to-many on purpose: one person can be both the Vendor Manager and the
    Product Manager, which the single `admin_permission` column could not say.
    """

    __tablename__ = "admin_user_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_admin_user_role"),
        Index("ix_admin_user_roles_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_roles.id", ondelete="CASCADE"), nullable=False
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    role: Mapped[AdminRole] = relationship(back_populates="assignments")


class AdminPermissionOverride(Base):
    """A permission granted to, or withheld from, one administrator directly.

    For the cases a role cannot express: the one Vendor Manager who may also see
    verification documents, or the Finance Manager who must not touch payouts
    while an investigation runs. `deny` wins over every role.
    """

    __tablename__ = "admin_permission_overrides"
    __table_args__ = (
        UniqueConstraint("user_id", "permission_code", name="uq_admin_permission_override"),
        Index("ix_admin_permission_overrides_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    permission_code: Mapped[str] = mapped_column(
        String(80), ForeignKey("admin_permissions.code", ondelete="CASCADE"), nullable=False
    )
    effect: Mapped[str] = mapped_column(String(10), nullable=False, default=OVERRIDE_GRANT)
    #: Why someone was given or refused this, for the audit trail to quote.
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AdminInvitation(Base):
    """An outstanding invitation to become an administrator.

    Only the hash of the token is stored. The raw token exists once, in the
    email that carries it, so a database leak cannot be used to accept
    invitations and nothing sensitive reaches a log.
    """

    __tablename__ = "admin_invitations"
    __table_args__ = (
        Index("ix_admin_invitations_email_status", "email", "status"),
        Index("ix_admin_invitations_status_expires", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    #: SHA-256 of the raw token. Unique so a hash collision cannot alias two
    #: invitations, and indexed because acceptance looks up by it.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=INVITE_PENDING)
    #: Internal note on what this person is being brought in to do.
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set once accepted, so the invitation and the account it created are linked.
    accepted_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: How many times the invitation email has gone out, for rate limiting.
    send_count: Mapped[int] = mapped_column(nullable=False, default=1)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    roles: Mapped[list[AdminInvitationRole]] = relationship(
        back_populates="invitation", cascade="all, delete-orphan", lazy="selectin"
    )


class AdminInvitationRole(Base):
    """A role the invitee will hold once they accept.

    Recorded up front and read at acceptance, so the access granted is the
    access the Super Admin reviewed — an invitee cannot influence it.
    """

    __tablename__ = "admin_invitation_roles"
    __table_args__ = (
        UniqueConstraint("invitation_id", "role_id", name="uq_admin_invitation_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invitation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_invitations.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_roles.id", ondelete="CASCADE"), nullable=False
    )

    invitation: Mapped[AdminInvitation] = relationship(back_populates="roles")
