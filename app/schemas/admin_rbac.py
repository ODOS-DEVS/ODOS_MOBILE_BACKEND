"""Request and response shapes for administrator and role management."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class PermissionRead(BaseModel):
    """One entry in the catalogue, as the permission editor shows it."""

    code: str
    module: str
    action: str
    label: str
    description: str
    is_sensitive: bool


class PermissionModuleRead(BaseModel):
    module: str
    label: str
    permissions: list[PermissionRead]


class RoleSummaryRead(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str
    is_system: bool
    is_active: bool
    permission_count: int
    #: How many administrators hold this role — the Super Admin needs to know
    #: the blast radius before editing or retiring it.
    administrator_count: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RoleDetailRead(RoleSummaryRead):
    permission_codes: list[str]
    #: Who holds it, so "which administrators does this change affect" is
    #: answerable without a second request.
    administrators: list[AdministratorBrief] = []


class AdministratorBrief(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    is_active: bool


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    permission_codes: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _trim(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("A role needs a name.")
        return trimmed


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    #: Omitted leaves permissions alone; an empty list genuinely clears them.
    permission_codes: list[str] | None = None


class AdministratorRead(BaseModel):
    id: uuid.UUID
    full_name: str
    email: str
    phone_number: str | None = None
    avatar_url: str | None = None
    is_active: bool
    is_super_admin: bool
    #: True once the account is governed by roles rather than the legacy band.
    is_role_managed: bool
    #: The old band, while any account is still on it. Null for role-managed.
    legacy_band: str | None = None
    roles: list[RoleSummaryRead] = []
    created_at: datetime
    last_login_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class PermissionOverrideRead(BaseModel):
    permission_code: str
    effect: str
    reason: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AdministratorDetailRead(AdministratorRead):
    #: Everything the account can do right now, after roles and overrides.
    effective_permissions: list[str] = []
    overrides: list[PermissionOverrideRead] = []
    #: code -> where it comes from ("Vendor Manager", "Granted directly"), so
    #: the editor can explain why a tick is there and why removing one from a
    #: role may not take it away.
    permission_sources: dict[str, list[str]] = {}


class AdministratorUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    phone_number: str | None = Field(default=None, max_length=32)


class RoleAssignment(BaseModel):
    """The complete set of roles the administrator should hold afterwards.

    Declarative rather than add/remove, so two Super Admins editing at once
    cannot interleave into a state neither chose.
    """

    role_ids: list[uuid.UUID] = Field(default_factory=list)


class OverrideSet(BaseModel):
    permission_code: str = Field(min_length=3, max_length=80)
    effect: str = Field(pattern="^(grant|deny)$")
    reason: str | None = Field(default=None, max_length=500)


class InvitationCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=160)
    email: EmailStr
    role_ids: list[uuid.UUID] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=500)


class InvitationRead(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    status: str
    note: str | None = None
    roles: list[RoleSummaryRead] = []
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    invited_by_name: str | None = None
    send_count: int
    last_sent_at: datetime | None = None
    created_at: datetime
    #: Never the token. It exists once, in the email.

    model_config = ConfigDict(from_attributes=True)


class InvitationAccept(BaseModel):
    """What the invitee submits. Carries no role or permission field on purpose.

    Access comes from the invitation row the Super Admin created, so nothing an
    invitee sends can widen what they are about to receive.
    """

    token: str = Field(min_length=10, max_length=256)
    password: str = Field(min_length=12, max_length=128)
    full_name: str | None = Field(default=None, min_length=2, max_length=160)


class InvitationPreviewRead(BaseModel):
    """Shown on the acceptance screen before a password is set."""

    email: str
    full_name: str
    role_names: list[str]
    expires_at: datetime
    invited_by_name: str | None = None


class MyPermissionsRead(BaseModel):
    """What the signed-in administrator can do, for the dashboard to adapt to."""

    is_super_admin: bool
    is_role_managed: bool
    roles: list[str]
    permissions: list[str]
    modules: list[str]


class AdminOverviewRead(BaseModel):
    total_administrators: int
    active_administrators: int
    inactive_administrators: int
    super_admins: int
    pending_invitations: int
    expired_invitations: int
    total_roles: int
    active_roles: int


class DeactivationRequest(BaseModel):
    #: Recorded in the audit trail. Required, because "why is this person
    #: locked out" is the first question asked afterwards.
    reason: str = Field(min_length=3, max_length=500)


RoleDetailRead.model_rebuild()
