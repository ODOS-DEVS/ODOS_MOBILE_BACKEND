"""Administrator and role management endpoints.

Guarding
--------
Every route carries `require_permission(...)` with the specific code it needs,
not a module-level check — the whole point of this system is that `admins.view`
and `admins.assign_roles` are different answers. The controller then applies the
rules that depend on *who* is being acted on (no self-administration, no
granting what you do not hold, super admin protection), because those cannot be
expressed as a dependency.

Two routes are deliberately unauthenticated: previewing and accepting an
invitation. They are gated by a single-use token instead, and neither can
influence the access being granted — that comes from the invitation row.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.controllers import admin_rbac_controller as ctl
from app.core.admin_permissions import require_permission
from app.core.database import get_db
from app.models import User
from app.schemas.admin_rbac import (
    AdministratorDetailRead,
    AdministratorRead,
    AdministratorUpdate,
    AdminOverviewRead,
    DeactivationRequest,
    InvitationCreate,
    InvitationRead,
    MyPermissionsRead,
    OverrideSet,
    PermissionModuleRead,
    RoleAssignment,
    RoleCreate,
    RoleDetailRead,
    RoleSummaryRead,
    RoleUpdate,
)
from app.schemas.user import MessageResponse

router = APIRouter(prefix="/admin", tags=["admin-rbac"])

# Read guards
ViewAdmins = Annotated[User, Depends(require_permission("admins.view"))]
# Role definitions are read by anyone who may view administrators *or* manage
# roles: the administrators list shows role names, so hiding them would make it
# unreadable.
ViewRoles = Annotated[User, Depends(require_permission("admins.view", "admins.manage_roles"))]

# Write guards
InviteAdmins = Annotated[User, Depends(require_permission("admins.invite"))]
UpdateAdmins = Annotated[User, Depends(require_permission("admins.update"))]
DeactivateAdmins = Annotated[User, Depends(require_permission("admins.deactivate"))]
AssignRoles = Annotated[User, Depends(require_permission("admins.assign_roles"))]
ManagePermissions = Annotated[User, Depends(require_permission("admins.manage_permissions"))]
ManageRoles = Annotated[User, Depends(require_permission("admins.manage_roles"))]


# ---------------------------------------------------------------------------
# The caller's own access
# ---------------------------------------------------------------------------


@router.get("/me/permissions", response_model=MyPermissionsRead)
def get_my_permissions(
    current_user: Annotated[User, Depends(require_permission("dashboard.view"))],
    db: Session = Depends(get_db),
):
    """What the signed-in administrator can do.

    The dashboard reads this to decide which navigation and actions exist. It is
    a convenience for the interface and never the security boundary: every
    protected operation is checked again server-side when it is called.

    Guarded on `dashboard.view` rather than `admins.view`, because every
    administrator needs to know their own access.
    """
    return ctl.my_permissions(db, current_user)


# ---------------------------------------------------------------------------
# Catalogue and overview
# ---------------------------------------------------------------------------


@router.get("/permissions/catalogue", response_model=list[PermissionModuleRead])
def get_permission_catalogue(current_user: ViewRoles):
    """Every permission, grouped by module, with descriptions."""
    _ = current_user
    return ctl.list_permission_catalogue()


@router.get("/administration/overview", response_model=AdminOverviewRead)
def get_admin_overview(current_user: ViewAdmins, db: Session = Depends(get_db)):
    _ = current_user
    return ctl.admin_overview(db)


# ---------------------------------------------------------------------------
# Administrators
# ---------------------------------------------------------------------------


@router.get("/administrators", response_model=list[AdministratorRead])
def get_administrators(
    current_user: ViewAdmins,
    db: Session = Depends(get_db),
    search: str | None = Query(default=None, max_length=120),
    role_id: uuid.UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    _ = current_user
    return ctl.list_administrators(
        db,
        search=search,
        role_id=role_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/administrators/{admin_id}", response_model=AdministratorDetailRead)
def get_administrator_detail(
    admin_id: uuid.UUID, current_user: ViewAdmins, db: Session = Depends(get_db)
):
    _ = current_user
    return ctl.get_administrator(db, admin_id)


@router.patch("/administrators/{admin_id}", response_model=AdministratorDetailRead)
def patch_administrator(
    admin_id: uuid.UUID,
    payload: AdministratorUpdate,
    current_user: UpdateAdmins,
    request: Request,
    db: Session = Depends(get_db),
):
    return ctl.update_administrator(db, current_user, admin_id, payload, request)


@router.put("/administrators/{admin_id}/roles", response_model=AdministratorDetailRead)
def put_administrator_roles(
    admin_id: uuid.UUID,
    payload: RoleAssignment,
    current_user: AssignRoles,
    request: Request,
    db: Session = Depends(get_db),
):
    """Replace this administrator's roles with exactly the set given."""
    return ctl.assign_roles(db, current_user, admin_id, payload, request)


@router.put("/administrators/{admin_id}/overrides", response_model=AdministratorDetailRead)
def put_administrator_override(
    admin_id: uuid.UUID,
    payload: OverrideSet,
    current_user: ManagePermissions,
    request: Request,
    db: Session = Depends(get_db),
):
    """Grant or deny one permission directly, outside their roles."""
    return ctl.set_override(db, current_user, admin_id, payload, request)


@router.delete(
    "/administrators/{admin_id}/overrides/{permission_code}",
    response_model=AdministratorDetailRead,
)
def delete_administrator_override(
    admin_id: uuid.UUID,
    permission_code: str,
    current_user: ManagePermissions,
    request: Request,
    db: Session = Depends(get_db),
):
    return ctl.clear_override(db, current_user, admin_id, permission_code, request)


@router.post("/administrators/{admin_id}/deactivate", response_model=AdministratorDetailRead)
def post_deactivate_administrator(
    admin_id: uuid.UUID,
    payload: DeactivationRequest,
    current_user: DeactivateAdmins,
    request: Request,
    db: Session = Depends(get_db),
):
    return ctl.set_administrator_active(
        db, current_user, admin_id, active=False, reason=payload.reason, request=request
    )


@router.post("/administrators/{admin_id}/reactivate", response_model=AdministratorDetailRead)
def post_reactivate_administrator(
    admin_id: uuid.UUID,
    payload: DeactivationRequest,
    current_user: DeactivateAdmins,
    request: Request,
    db: Session = Depends(get_db),
):
    return ctl.set_administrator_active(
        db, current_user, admin_id, active=True, reason=payload.reason, request=request
    )


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


@router.get("/roles", response_model=list[RoleSummaryRead])
def get_roles(
    current_user: ViewRoles,
    db: Session = Depends(get_db),
    include_inactive: bool = Query(default=True),
):
    _ = current_user
    return ctl.list_roles(db, include_inactive=include_inactive)


@router.get("/roles/{role_id}", response_model=RoleDetailRead)
def get_role_detail(role_id: uuid.UUID, current_user: ViewRoles, db: Session = Depends(get_db)):
    _ = current_user
    return ctl.get_role(db, role_id)


@router.post("/roles", response_model=RoleDetailRead, status_code=status.HTTP_201_CREATED)
def post_role(
    payload: RoleCreate,
    current_user: ManageRoles,
    request: Request,
    db: Session = Depends(get_db),
):
    return ctl.create_role(db, current_user, payload, request)


@router.patch("/roles/{role_id}", response_model=RoleDetailRead)
def patch_role(
    role_id: uuid.UUID,
    payload: RoleUpdate,
    current_user: ManageRoles,
    request: Request,
    db: Session = Depends(get_db),
):
    return ctl.update_role(db, current_user, role_id, payload, request)


@router.post(
    "/roles/{role_id}/duplicate", response_model=RoleDetailRead, status_code=status.HTTP_201_CREATED
)
def post_duplicate_role(
    role_id: uuid.UUID,
    current_user: ManageRoles,
    request: Request,
    db: Session = Depends(get_db),
):
    return ctl.duplicate_role(db, current_user, role_id, request)


@router.delete("/roles/{role_id}", response_model=MessageResponse)
def delete_role_route(
    role_id: uuid.UUID,
    current_user: ManageRoles,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a custom role. Refuses while anyone still holds it."""
    ctl.delete_role(db, current_user, role_id, request)
    return MessageResponse(message="Role deleted.")


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


@router.get("/invitations", response_model=list[InvitationRead])
def get_invitations(
    current_user: ViewAdmins,
    db: Session = Depends(get_db),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    _ = current_user
    return ctl.list_invitations(db, status_filter=status_filter, limit=limit, offset=offset)


@router.post("/invitations", response_model=InvitationRead, status_code=status.HTTP_201_CREATED)
def post_invitation(
    payload: InvitationCreate,
    current_user: InviteAdmins,
    request: Request,
    db: Session = Depends(get_db),
):
    """Invite an administrator.

    The email goes out before the row commits, so a delivery failure leaves no
    invitation behind and the caller is told plainly rather than shown a pending
    invitation nobody received.
    """
    return ctl.invite_administrator(db, current_user, payload, request)


@router.post("/invitations/{invitation_id}/resend", response_model=InvitationRead)
def post_resend_invitation(
    invitation_id: uuid.UUID,
    current_user: InviteAdmins,
    request: Request,
    db: Session = Depends(get_db),
):
    """Mint and send a fresh token. The previous link stops working."""
    return ctl.resend_invitation(db, current_user, invitation_id, request)


@router.post("/invitations/{invitation_id}/revoke", response_model=InvitationRead)
def post_revoke_invitation(
    invitation_id: uuid.UUID,
    current_user: InviteAdmins,
    request: Request,
    db: Session = Depends(get_db),
):
    return ctl.revoke_invitation(db, current_user, invitation_id, request)
