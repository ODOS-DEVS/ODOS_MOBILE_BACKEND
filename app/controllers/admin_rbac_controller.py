"""Managing administrators, roles and permissions.

The guards here are the point of the module, so they are stated once:

* **No self-administration.** Nobody edits their own roles or overrides, or
  deactivates themselves. Otherwise `admins.assign_roles` is the only
  permission anyone ever needs.
* **No granting what you do not hold.** A delegated administrator with
  `admins.assign_roles` cannot assign a role containing permissions they lack,
  and cannot write an override for one. Without this rule, the power to
  delegate is the power to promote yourself by proxy.
* **Super Admin is untouchable from here.** It is a flag on the user, not a
  role, so no role edit reaches it; and only a super admin may act on another
  super admin's account at all.
* **The last active super admin cannot be deactivated.** Checked at the moment
  of the write, not from a cached count.
* **A role in use is not silently deleted.** Deactivation is offered instead,
  and deletion refuses while anyone holds it.

Every mutation writes an audit row through `record_admin_event`, with the before
and after state where there is one. Tokens, passwords and hashes are never
included in that row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core import event_types
from app.core.admin_permission_catalogue import (
    MODULE_LABELS,
    PERMISSIONS_BY_CODE,
    PERMISSIONS_BY_MODULE,
)
from app.core.security import hash_password
from app.models import User, UserRole
from app.models.admin_rbac import (
    INVITE_ACCEPTED,
    INVITE_EXPIRED,
    INVITE_PENDING,
    INVITE_REVOKED,
    OVERRIDE_DENY,
    AdminInvitation,
    AdminInvitationRole,
    AdminPermissionOverride,
    AdminRole,
    AdminRolePermission,
    AdminUserRole,
)
from app.schemas.admin_rbac import (
    AdministratorBrief,
    AdministratorDetailRead,
    AdministratorRead,
    AdminOverviewRead,
    InvitationPreviewRead,
    InvitationRead,
    MyPermissionsRead,
    PermissionModuleRead,
    PermissionOverrideRead,
    PermissionRead,
    RoleDetailRead,
    RoleSummaryRead,
)
from app.services import admin_invitation_service as invites
from app.services.admin_rbac import (
    RBAC_MANAGED_FLAG,
    SUPER_ADMIN_FLAG,
    count_active_super_admins,
    effective_permissions,
    is_rbac_managed,
    is_super_admin,
    module_access,
    permission_sources,
    role_permission_codes,
)
from app.services.event_log_service import record_admin_event

# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def _forbid(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _not_found(detail: str = "That administrator was not found.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


def _load_admin(db: Session, admin_id) -> User:
    user = db.get(User, admin_id)
    if user is None or user.role != UserRole.ADMIN:
        raise _not_found()
    return user


def _guard_not_self(actor: User, target: User, what: str) -> None:
    if actor.id == target.id:
        raise _forbid(
            f"You cannot {what} your own account. Ask another administrator to do it."
        )


def _guard_target_not_super(actor: User, target: User) -> None:
    """Only a super admin may act on a super admin."""
    if is_super_admin(target) and not is_super_admin(actor):
        raise _forbid("Only a super admin can manage another super admin's account.")


def _guard_can_grant(db: Session, actor: User, codes: set[str]) -> None:
    """Refuse to hand out access the actor does not hold.

    Super admins hold the whole catalogue so this never bites them. For anyone
    else it is the difference between delegating access and manufacturing it.
    """
    if is_super_admin(actor):
        return
    held = effective_permissions(db, actor)
    excess = sorted(codes - held)
    if excess:
        labels = [PERMISSIONS_BY_CODE[c].label for c in excess[:3] if c in PERMISSIONS_BY_CODE]
        shown = ", ".join(labels) or f"{len(excess)} permissions"
        raise _forbid(
            "You can only grant access you hold yourself. You do not have: " + shown
        )


def _validate_codes(codes: list[str]) -> set[str]:
    cleaned = {code.strip() for code in codes if code and code.strip()}
    unknown = sorted(cleaned - set(PERMISSIONS_BY_CODE))
    if unknown:
        raise _invalid(f"Unrecognised permissions: {', '.join(unknown[:5])}")
    return cleaned


def _request_context(request: Request | None) -> dict[str, str | None]:
    if request is None:
        return {"ip_address": None, "user_agent": None}
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


def _audit(
    db: Session,
    actor: User,
    *,
    event_type: str,
    action: str,
    entity_id: str | None = None,
    entity_type: str = "admin_user",
    metadata: dict | None = None,
    before: dict | None = None,
    after: dict | None = None,
    request: Request | None = None,
) -> None:
    record_admin_event(
        db,
        admin_user=actor,
        event_type=event_type,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
        before_state=before,
        after_state=after,
        commit=False,
        **_request_context(request),
    )


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _role_counts(db: Session) -> dict[uuid.UUID, int]:
    rows = db.execute(
        select(AdminUserRole.role_id, func.count()).group_by(AdminUserRole.role_id)
    ).all()
    return dict(rows)


def _role_summary(role: AdminRole, holders: int) -> RoleSummaryRead:
    return RoleSummaryRead(
        id=role.id,
        slug=role.slug,
        name=role.name,
        description=role.description or "",
        is_system=role.is_system,
        is_active=role.is_active,
        permission_count=len(role.permissions),
        administrator_count=holders,
        created_at=role.created_at,
    )


def _roles_for_user(db: Session, user_id) -> list[AdminRole]:
    return list(
        db.scalars(
            select(AdminRole)
            .join(AdminUserRole, AdminUserRole.role_id == AdminRole.id)
            .where(AdminUserRole.user_id == user_id)
            .options(selectinload(AdminRole.permissions))
            .order_by(AdminRole.name)
        ).all()
    )


def _administrator_read(db: Session, user: User, counts: dict) -> AdministratorRead:
    band = (user.admin_permission or "").strip().lower()
    managed = is_rbac_managed(db, user)
    return AdministratorRead(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone_number=getattr(user, "phone_number", None),
        avatar_url=getattr(user, "avatar_url", None),
        is_active=bool(user.is_active),
        is_super_admin=is_super_admin(user),
        is_role_managed=managed,
        legacy_band=None if (managed or band == SUPER_ADMIN_FLAG) else (band or None),
        roles=[_role_summary(r, counts.get(r.id, 0)) for r in _roles_for_user(db, user.id)],
        created_at=user.created_at,
        last_login_at=getattr(user, "last_login_at", None),
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_permission_catalogue() -> list[PermissionModuleRead]:
    """The catalogue, grouped for the permission editor.

    Descriptions ride along because a tick box labelled `vendors.approve` with
    no explanation is how a Super Admin grants something they did not intend.
    """
    return [
        PermissionModuleRead(
            module=module,
            label=MODULE_LABELS[module],
            permissions=[
                PermissionRead(
                    code=p.code,
                    module=p.module,
                    action=p.action,
                    label=p.label,
                    description=p.description,
                    is_sensitive=p.sensitive,
                )
                for p in permissions
            ],
        )
        for module, permissions in PERMISSIONS_BY_MODULE.items()
        if permissions
    ]


def my_permissions(db: Session, current_user: User) -> MyPermissionsRead:
    return MyPermissionsRead(
        is_super_admin=is_super_admin(current_user),
        is_role_managed=is_rbac_managed(db, current_user),
        roles=[r.name for r in _roles_for_user(db, current_user.id)],
        permissions=sorted(effective_permissions(db, current_user)),
        modules=sorted(module_access(db, current_user)),
    )


def list_administrators(
    db: Session,
    *,
    search: str | None = None,
    role_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AdministratorRead]:
    query = select(User).where(User.role == UserRole.ADMIN)

    if search and search.strip():
        needle = f"%{search.strip().lower()}%"
        query = query.where(
            func.lower(User.full_name).like(needle) | func.lower(User.email).like(needle)
        )
    if role_id is not None:
        query = query.join(AdminUserRole, AdminUserRole.user_id == User.id).where(
            AdminUserRole.role_id == role_id
        )
    cleaned_status = (status_filter or "").strip().lower()
    if cleaned_status == "active":
        query = query.where(User.is_active.is_(True))
    elif cleaned_status in {"inactive", "deactivated", "suspended"}:
        query = query.where(User.is_active.is_(False))

    rows = list(
        db.scalars(query.order_by(User.created_at.desc()).offset(offset).limit(limit)).all()
    )
    counts = _role_counts(db)
    return [_administrator_read(db, user, counts) for user in rows]


def get_administrator(db: Session, admin_id) -> AdministratorDetailRead:
    user = _load_admin(db, admin_id)
    counts = _role_counts(db)
    base = _administrator_read(db, user, counts)
    overrides = db.scalars(
        select(AdminPermissionOverride).where(AdminPermissionOverride.user_id == user.id)
    ).all()
    return AdministratorDetailRead(
        **base.model_dump(),
        effective_permissions=sorted(effective_permissions(db, user)),
        overrides=[PermissionOverrideRead.model_validate(o) for o in overrides],
        permission_sources=permission_sources(db, user),
    )


def admin_overview(db: Session) -> AdminOverviewRead:
    invites.expire_stale_invitations(db)
    admins = list(db.scalars(select(User).where(User.role == UserRole.ADMIN)).all())
    roles = list(db.scalars(select(AdminRole)).all())
    pending = db.scalar(
        select(func.count())
        .select_from(AdminInvitation)
        .where(AdminInvitation.status == INVITE_PENDING)
    )
    expired = db.scalar(
        select(func.count())
        .select_from(AdminInvitation)
        .where(AdminInvitation.status == INVITE_EXPIRED)
    )
    return AdminOverviewRead(
        total_administrators=len(admins),
        active_administrators=sum(1 for a in admins if a.is_active),
        inactive_administrators=sum(1 for a in admins if not a.is_active),
        super_admins=sum(1 for a in admins if is_super_admin(a)),
        pending_invitations=pending or 0,
        expired_invitations=expired or 0,
        total_roles=len(roles),
        active_roles=sum(1 for r in roles if r.is_active),
    )


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def list_roles(db: Session, *, include_inactive: bool = True) -> list[RoleSummaryRead]:
    query = select(AdminRole).options(selectinload(AdminRole.permissions))
    if not include_inactive:
        query = query.where(AdminRole.is_active.is_(True))
    roles = list(db.scalars(query.order_by(AdminRole.is_system.desc(), AdminRole.name)).all())
    counts = _role_counts(db)
    return [_role_summary(r, counts.get(r.id, 0)) for r in roles]


def get_role(db: Session, role_id) -> RoleDetailRead:
    role = db.get(AdminRole, role_id)
    if role is None:
        raise _not_found("That role was not found.")
    counts = _role_counts(db)
    holders = list(
        db.scalars(
            select(User)
            .join(AdminUserRole, AdminUserRole.user_id == User.id)
            .where(AdminUserRole.role_id == role.id)
            .order_by(User.full_name)
        ).all()
    )
    return RoleDetailRead(
        **_role_summary(role, counts.get(role.id, 0)).model_dump(),
        permission_codes=sorted(row.permission_code for row in role.permissions),
        administrators=[
            AdministratorBrief(
                id=u.id, full_name=u.full_name, email=u.email, is_active=bool(u.is_active)
            )
            for u in holders
        ],
    )


def _slugify_role(name: str) -> str:
    base = "".join(ch if ch.isalnum() else "_" for ch in name.strip().lower())
    while "__" in base:
        base = base.replace("__", "_")
    return base.strip("_")[:60] or "role"


def create_role(db: Session, actor: User, payload, request: Request | None = None) -> RoleDetailRead:
    codes = _validate_codes(payload.permission_codes)
    _guard_can_grant(db, actor, codes)

    slug = _slugify_role(payload.name)
    if db.scalar(select(AdminRole).where(AdminRole.slug == slug)):
        slug = f"{slug}_{uuid.uuid4().hex[:6]}"

    role = AdminRole(
        slug=slug,
        name=payload.name.strip(),
        description=(payload.description or "").strip(),
        is_system=False,
        is_active=True,
        created_by=actor.id,
    )
    db.add(role)
    db.flush()
    for code in sorted(codes):
        db.add(AdminRolePermission(role_id=role.id, permission_code=code))

    _audit(
        db, actor,
        event_type=event_types.ADMIN_RBAC_ROLE_CREATED,
        action="Created role",
        entity_type="admin_role",
        entity_id=str(role.id),
        metadata={"slug": slug, "name": role.name},
        after={"permission_codes": sorted(codes)},
        request=request,
    )
    db.commit()
    return get_role(db, role.id)


def update_role(db: Session, actor: User, role_id, payload, request: Request | None = None) -> RoleDetailRead:
    role = db.get(AdminRole, role_id)
    if role is None:
        raise _not_found("That role was not found.")

    before = {
        "name": role.name,
        "description": role.description,
        "is_active": role.is_active,
        "permission_codes": sorted(row.permission_code for row in role.permissions),
    }

    if payload.name is not None:
        role.name = payload.name.strip()
    if payload.description is not None:
        role.description = payload.description.strip()
    if payload.is_active is not None:
        if not payload.is_active:
            holders = _role_counts(db).get(role.id, 0)
            if holders:
                # Deactivating is allowed and takes effect immediately, but the
                # caller is told what it costs rather than discovering it from
                # support tickets.
                _audit(
                    db, actor,
                    event_type=event_types.ADMIN_RBAC_ROLE_DEACTIVATED,
                    action="Deactivated a role still in use",
                    entity_type="admin_role",
                    entity_id=str(role.id),
                    metadata={"affected_administrators": holders},
                    request=request,
                )
        role.is_active = payload.is_active

    if payload.permission_codes is not None:
        codes = _validate_codes(payload.permission_codes)
        current = {row.permission_code for row in role.permissions}
        # Only newly added codes need the escalation check: taking a permission
        # away is never an escalation.
        _guard_can_grant(db, actor, codes - current)
        for row in list(role.permissions):
            if row.permission_code not in codes:
                db.delete(row)
        for code in sorted(codes - current):
            db.add(AdminRolePermission(role_id=role.id, permission_code=code))

    role.updated_at = datetime.now(UTC)
    _audit(
        db, actor,
        event_type=event_types.ADMIN_RBAC_ROLE_UPDATED,
        action="Updated role",
        entity_type="admin_role",
        entity_id=str(role.id),
        before=before,
        after={
            "name": role.name,
            "description": role.description,
            "is_active": role.is_active,
            "permission_codes": sorted(payload.permission_codes)
            if payload.permission_codes is not None
            else before["permission_codes"],
        },
        request=request,
    )
    db.commit()
    return get_role(db, role.id)


def duplicate_role(db: Session, actor: User, role_id, request: Request | None = None) -> RoleDetailRead:
    """Copy a role's permissions into a new, editable one.

    The usual way to build a variant of a system role without touching the
    original that other administrators depend on.
    """
    source = db.get(AdminRole, role_id)
    if source is None:
        raise _not_found("That role was not found.")
    codes = role_permission_codes(db, source.id)
    _guard_can_grant(db, actor, codes)

    role = AdminRole(
        slug=f"{_slugify_role(source.name)}_copy_{uuid.uuid4().hex[:6]}",
        name=f"{source.name} (copy)",
        description=source.description or "",
        is_system=False,
        is_active=True,
        created_by=actor.id,
    )
    db.add(role)
    db.flush()
    for code in sorted(codes):
        db.add(AdminRolePermission(role_id=role.id, permission_code=code))

    _audit(
        db, actor,
        event_type=event_types.ADMIN_RBAC_ROLE_CREATED,
        action="Duplicated role",
        entity_type="admin_role",
        entity_id=str(role.id),
        metadata={"copied_from": source.slug},
        after={"permission_codes": sorted(codes)},
        request=request,
    )
    db.commit()
    return get_role(db, role.id)


def delete_role(db: Session, actor: User, role_id, request: Request | None = None) -> None:
    role = db.get(AdminRole, role_id)
    if role is None:
        raise _not_found("That role was not found.")
    if role.is_system:
        raise _conflict(
            "Built-in roles cannot be deleted. Deactivate it instead if you have stopped using it."
        )
    holders = _role_counts(db).get(role.id, 0)
    if holders:
        raise _conflict(
            f"{holders} administrator{'s' if holders != 1 else ''} still hold this role. "
            "Move them to another role first, or deactivate this one."
        )

    _audit(
        db, actor,
        event_type=event_types.ADMIN_RBAC_ROLE_DEACTIVATED,
        action="Deleted role",
        entity_type="admin_role",
        entity_id=str(role.id),
        before={
            "slug": role.slug,
            "name": role.name,
            "permission_codes": sorted(r.permission_code for r in role.permissions),
        },
        request=request,
    )
    db.delete(role)
    db.commit()


# ---------------------------------------------------------------------------
# Assigning roles and overrides
# ---------------------------------------------------------------------------


def assign_roles(db: Session, actor: User, admin_id, payload, request: Request | None = None) -> AdministratorDetailRead:
    """Replace the administrator's roles with exactly `payload.role_ids`."""
    target = _load_admin(db, admin_id)
    _guard_not_self(actor, target, "change the roles on")
    _guard_target_not_super(actor, target)

    if is_super_admin(target):
        raise _conflict(
            "Super admin access is a property of the account, not a role, so roles do "
            "not change what this administrator can do."
        )

    wanted_ids = set(payload.role_ids)
    roles = list(db.scalars(select(AdminRole).where(AdminRole.id.in_(wanted_ids))).all()) if wanted_ids else []
    if len(roles) != len(wanted_ids):
        raise _invalid("One or more of those roles does not exist.")

    inactive = [r.name for r in roles if not r.is_active]
    if inactive:
        raise _invalid(
            "These roles are deactivated and cannot be assigned: " + ", ".join(inactive)
        )

    granting: set[str] = set()
    for role in roles:
        granting |= role_permission_codes(db, role.id)
    _guard_can_grant(db, actor, granting)

    existing = {row.role_id: row for row in db.scalars(
        select(AdminUserRole).where(AdminUserRole.user_id == target.id)
    ).all()}
    before_perms = sorted(effective_permissions(db, target))
    before_roles = sorted(r.name for r in _roles_for_user(db, target.id))

    for role_id, row in existing.items():
        if role_id not in wanted_ids:
            db.delete(row)
    for role in roles:
        if role.id not in existing:
            db.add(AdminUserRole(user_id=target.id, role_id=role.id, assigned_by=actor.id))

    # From here the account is governed by roles, never by the old band. Without
    # this, removing every role later would hand them their band back.
    if not is_super_admin(target):
        target.admin_permission = RBAC_MANAGED_FLAG

    db.flush()
    after_perms = sorted(effective_permissions(db, target))

    _audit(
        db, actor,
        event_type=event_types.ADMIN_ROLES_ASSIGNED,
        action="Changed assigned roles",
        entity_id=str(target.id),
        metadata={
            "administrator": target.email,
            "granted": sorted(set(after_perms) - set(before_perms)),
            "revoked": sorted(set(before_perms) - set(after_perms)),
        },
        before={"roles": before_roles, "permissions": before_perms},
        after={"roles": sorted(r.name for r in roles), "permissions": after_perms},
        request=request,
    )
    db.commit()
    return get_administrator(db, target.id)


def set_override(db: Session, actor: User, admin_id, payload, request: Request | None = None) -> AdministratorDetailRead:
    """Grant or deny one permission to one administrator directly."""
    target = _load_admin(db, admin_id)
    _guard_not_self(actor, target, "change the permissions on")
    _guard_target_not_super(actor, target)

    code = payload.permission_code.strip()
    if code not in PERMISSIONS_BY_CODE:
        raise _invalid(f"Unrecognised permission: {code}")
    effect = payload.effect.strip().lower()

    # Only a grant can escalate. A deny is always allowed — anyone who may
    # manage permissions may take one away.
    if effect != OVERRIDE_DENY:
        _guard_can_grant(db, actor, {code})

    before_perms = sorted(effective_permissions(db, target))
    row = db.scalar(
        select(AdminPermissionOverride).where(
            AdminPermissionOverride.user_id == target.id,
            AdminPermissionOverride.permission_code == code,
        )
    )
    previous_effect = row.effect if row else None
    if row is None:
        row = AdminPermissionOverride(
            user_id=target.id,
            permission_code=code,
            effect=effect,
            reason=payload.reason,
            created_by=actor.id,
        )
        db.add(row)
    else:
        row.effect = effect
        row.reason = payload.reason
        row.created_by = actor.id

    if not is_super_admin(target):
        target.admin_permission = RBAC_MANAGED_FLAG

    db.flush()
    after_perms = sorted(effective_permissions(db, target))

    _audit(
        db, actor,
        event_type=event_types.ADMIN_PERMISSION_OVERRIDDEN,
        action=f"Set a permission override to {effect}",
        entity_id=str(target.id),
        metadata={
            "administrator": target.email,
            "permission": code,
            "permission_label": PERMISSIONS_BY_CODE[code].label,
            "effect": effect,
            "previous_effect": previous_effect,
            "reason": payload.reason,
            "is_sensitive": PERMISSIONS_BY_CODE[code].sensitive,
        },
        before={"permissions": before_perms},
        after={"permissions": after_perms},
        request=request,
    )
    db.commit()
    return get_administrator(db, target.id)


def clear_override(db: Session, actor: User, admin_id, code: str, request: Request | None = None) -> AdministratorDetailRead:
    target = _load_admin(db, admin_id)
    _guard_not_self(actor, target, "change the permissions on")
    _guard_target_not_super(actor, target)

    row = db.scalar(
        select(AdminPermissionOverride).where(
            AdminPermissionOverride.user_id == target.id,
            AdminPermissionOverride.permission_code == code.strip(),
        )
    )
    if row is None:
        raise _not_found("There is no override for that permission.")

    before_perms = sorted(effective_permissions(db, target))
    removed_effect = row.effect
    db.delete(row)
    db.flush()

    _audit(
        db, actor,
        event_type=event_types.ADMIN_PERMISSION_OVERRIDDEN,
        action="Removed a permission override",
        entity_id=str(target.id),
        metadata={
            "administrator": target.email,
            "permission": code,
            "removed_effect": removed_effect,
        },
        before={"permissions": before_perms},
        after={"permissions": sorted(effective_permissions(db, target))},
        request=request,
    )
    db.commit()
    return get_administrator(db, target.id)


def update_administrator(db: Session, actor: User, admin_id, payload, request: Request | None = None) -> AdministratorDetailRead:
    target = _load_admin(db, admin_id)
    _guard_target_not_super(actor, target)

    before = {"full_name": target.full_name, "phone_number": getattr(target, "phone_number", None)}
    if payload.full_name is not None:
        target.full_name = payload.full_name.strip()
    if payload.phone_number is not None:
        target.phone_number = payload.phone_number.strip() or None

    _audit(
        db, actor,
        event_type=event_types.ADMIN_ACCOUNT_UPDATED,
        action="Updated administrator details",
        entity_id=str(target.id),
        metadata={"administrator": target.email},
        before=before,
        after={"full_name": target.full_name, "phone_number": getattr(target, "phone_number", None)},
        request=request,
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("Those details are already in use by another account.") from exc
    return get_administrator(db, target.id)


def set_administrator_active(
    db: Session, actor: User, admin_id, *, active: bool, reason: str, request: Request | None = None
) -> AdministratorDetailRead:
    target = _load_admin(db, admin_id)
    _guard_not_self(actor, target, "deactivate" if not active else "reactivate")
    _guard_target_not_super(actor, target)

    # Checked against the database at the moment of the write, never a cached count.
    if (
        not active
        and is_super_admin(target)
        and count_active_super_admins(db, excluding=target.id) == 0
    ):
        raise _conflict(
            "This is the last active super admin. Promote another administrator to "
            "super admin before deactivating this account, or there would be no way "
            "back into the platform."
        )

    if bool(target.is_active) == active:
        raise _conflict(
            "That administrator is already " + ("active." if active else "deactivated.")
        )

    target.is_active = active
    _audit(
        db, actor,
        event_type=(
            event_types.ADMIN_ACCOUNT_REACTIVATED if active
            else event_types.ADMIN_ACCOUNT_DEACTIVATED
        ),
        action="Reactivated administrator" if active else "Deactivated administrator",
        entity_id=str(target.id),
        metadata={"administrator": target.email, "reason": reason},
        before={"is_active": not active},
        after={"is_active": active},
        request=request,
    )
    db.commit()
    return get_administrator(db, target.id)


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def _invitation_read(db: Session, invitation: AdminInvitation) -> InvitationRead:
    counts = _role_counts(db)
    roles = list(
        db.scalars(
            select(AdminRole)
            .join(AdminInvitationRole, AdminInvitationRole.role_id == AdminRole.id)
            .where(AdminInvitationRole.invitation_id == invitation.id)
            .options(selectinload(AdminRole.permissions))
            .order_by(AdminRole.name)
        ).all()
    )
    inviter = db.get(User, invitation.invited_by) if invitation.invited_by else None
    return InvitationRead(
        id=invitation.id,
        email=invitation.email,
        full_name=invitation.full_name,
        status=invitation.status,
        note=invitation.note,
        roles=[_role_summary(r, counts.get(r.id, 0)) for r in roles],
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        revoked_at=invitation.revoked_at,
        invited_by_name=inviter.full_name if inviter else None,
        send_count=invitation.send_count or 0,
        last_sent_at=invitation.last_sent_at,
        created_at=invitation.created_at,
    )


def list_invitations(
    db: Session, *, status_filter: str | None = None, limit: int = 50, offset: int = 0
) -> list[InvitationRead]:
    invites.expire_stale_invitations(db)
    query = select(AdminInvitation)
    cleaned = (status_filter or "").strip().lower()
    if cleaned and cleaned != "all":
        query = query.where(AdminInvitation.status == cleaned)
    rows = list(
        db.scalars(
            query.order_by(AdminInvitation.created_at.desc()).offset(offset).limit(limit)
        ).all()
    )
    return [_invitation_read(db, row) for row in rows]


def invite_administrator(db: Session, actor: User, payload, request: Request | None = None) -> InvitationRead:
    """Create an invitation and email it.

    The email is sent *before* the row is committed. If sending fails the whole
    thing rolls back, so the list never shows an invitation that was never
    delivered and nobody waits for a message that does not exist.
    """
    email = payload.email.strip().lower()

    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise _conflict("An account with that email address already exists.")

    invites.expire_stale_invitations(db)
    if db.scalar(
        select(AdminInvitation).where(
            AdminInvitation.email == email, AdminInvitation.status == INVITE_PENDING
        )
    ):
        raise _conflict(
            "There is already a pending invitation for that address. Resend or revoke it."
        )

    wanted = set(payload.role_ids)
    roles = list(db.scalars(select(AdminRole).where(AdminRole.id.in_(wanted))).all()) if wanted else []
    if len(roles) != len(wanted):
        raise _invalid("One or more of those roles does not exist.")
    inactive = [r.name for r in roles if not r.is_active]
    if inactive:
        raise _invalid("These roles are deactivated: " + ", ".join(inactive))

    granting: set[str] = set()
    for role in roles:
        granting |= role_permission_codes(db, role.id)
    _guard_can_grant(db, actor, granting)

    raw_token, token_hash = invites.generate_token()
    invitation = AdminInvitation(
        email=email,
        full_name=payload.full_name.strip(),
        token_hash=token_hash,
        status=INVITE_PENDING,
        note=(payload.note or "").strip() or None,
        expires_at=invites.invitation_expiry(),
        invited_by=actor.id,
        send_count=1,
        last_sent_at=datetime.now(UTC),
    )
    db.add(invitation)
    db.flush()
    for role in roles:
        db.add(AdminInvitationRole(invitation_id=invitation.id, role_id=role.id))

    # Raises if email is not configured, which rolls the invitation back.
    invites.send_invitation_email(
        to_email=email,
        to_name=invitation.full_name,
        inviter_name=actor.full_name,
        role_names=[r.name for r in roles],
        raw_token=raw_token,
        note=invitation.note,
    )

    _audit(
        db, actor,
        event_type=event_types.ADMIN_INVITED,
        action="Invited an administrator",
        entity_type="admin_invitation",
        entity_id=str(invitation.id),
        # The token is deliberately absent.
        metadata={
            "email": email,
            "roles": [r.name for r in roles],
            "permissions_granted": sorted(granting),
            "expires_at": invitation.expires_at.isoformat(),
        },
        request=request,
    )
    db.commit()
    return _invitation_read(db, invitation)


def resend_invitation(db: Session, actor: User, invitation_id, request: Request | None = None) -> InvitationRead:
    """Send a fresh token for a pending invitation.

    A new token is minted and the old hash replaced, so a link that leaked
    before the resend stops working.
    """
    invitation = db.get(AdminInvitation, invitation_id)
    if invitation is None:
        raise _not_found("That invitation was not found.")

    allowed, why = invites.can_resend(invitation)
    if not allowed:
        raise _conflict(why or "That invitation cannot be resent.")

    raw_token, token_hash = invites.generate_token()
    invitation.token_hash = token_hash
    invitation.expires_at = invites.invitation_expiry()
    invitation.send_count = (invitation.send_count or 0) + 1
    invitation.last_sent_at = datetime.now(UTC)

    roles = list(
        db.scalars(
            select(AdminRole)
            .join(AdminInvitationRole, AdminInvitationRole.role_id == AdminRole.id)
            .where(AdminInvitationRole.invitation_id == invitation.id)
        ).all()
    )
    inviter = db.get(User, invitation.invited_by) if invitation.invited_by else None

    invites.send_invitation_email(
        to_email=invitation.email,
        to_name=invitation.full_name,
        inviter_name=(inviter.full_name if inviter else actor.full_name),
        role_names=[r.name for r in roles],
        raw_token=raw_token,
        note=invitation.note,
    )

    _audit(
        db, actor,
        event_type=event_types.ADMIN_INVITATION_RESENT,
        action="Resent an administrator invitation",
        entity_type="admin_invitation",
        entity_id=str(invitation.id),
        metadata={"email": invitation.email, "send_count": invitation.send_count},
        request=request,
    )
    db.commit()
    return _invitation_read(db, invitation)


def revoke_invitation(db: Session, actor: User, invitation_id, request: Request | None = None) -> InvitationRead:
    invitation = db.get(AdminInvitation, invitation_id)
    if invitation is None:
        raise _not_found("That invitation was not found.")
    if invitation.status != INVITE_PENDING:
        raise _conflict("Only a pending invitation can be revoked.")

    invitation.status = INVITE_REVOKED
    invitation.revoked_at = datetime.now(UTC)
    # Replaced rather than kept: the outstanding link must stop working now.
    invitation.token_hash = invites.hash_token(f"revoked-{uuid.uuid4().hex}")

    _audit(
        db, actor,
        event_type=event_types.ADMIN_INVITATION_REVOKED,
        action="Revoked an administrator invitation",
        entity_type="admin_invitation",
        entity_id=str(invitation.id),
        metadata={"email": invitation.email},
        request=request,
    )
    db.commit()
    return _invitation_read(db, invitation)


def preview_invitation(db: Session, raw_token: str) -> InvitationPreviewRead:
    """What the acceptance page shows before a password is set. Unauthenticated."""
    invitation = invites.find_pending_by_token(db, raw_token)
    if invitation is None:
        raise _not_found("That invitation link is not valid or has expired.")
    roles = list(
        db.scalars(
            select(AdminRole)
            .join(AdminInvitationRole, AdminInvitationRole.role_id == AdminRole.id)
            .where(AdminInvitationRole.invitation_id == invitation.id)
            .order_by(AdminRole.name)
        ).all()
    )
    inviter = db.get(User, invitation.invited_by) if invitation.invited_by else None
    return InvitationPreviewRead(
        email=invitation.email,
        full_name=invitation.full_name,
        role_names=[r.name for r in roles],
        expires_at=invitation.expires_at,
        invited_by_name=inviter.full_name if inviter else None,
    )


def accept_invitation(db: Session, payload, request: Request | None = None) -> User:
    """Create the administrator account. Unauthenticated, token-gated.

    The roles come from the invitation row, never from the request, so an
    invitee cannot widen what they were offered. The token is consumed in the
    same transaction that creates the account, so it cannot be replayed.
    """
    invitation = invites.find_pending_by_token(db, payload.token)
    if invitation is None:
        raise _not_found("That invitation link is not valid or has expired.")

    if db.scalar(select(User).where(func.lower(User.email) == invitation.email)):
        raise _conflict("An account with that email address already exists.")

    roles = list(
        db.scalars(
            select(AdminRole)
            .join(AdminInvitationRole, AdminInvitationRole.role_id == AdminRole.id)
            .where(AdminInvitationRole.invitation_id == invitation.id)
        ).all()
    )

    user = User(
        full_name=(payload.full_name or invitation.full_name).strip(),
        email=invitation.email,
        hashed_password=hash_password(payload.password),
        role=UserRole.ADMIN,
        # Role-governed from the first second. Never a band, and never
        # super_admin — that cannot be reached through an invitation.
        admin_permission=RBAC_MANAGED_FLAG,
        is_active=True,
        # The invitation arrived at this address and the token proves it.
        is_verified=True,
    )
    db.add(user)
    db.flush()

    for role in roles:
        if role.is_active:
            db.add(AdminUserRole(user_id=user.id, role_id=role.id, assigned_by=invitation.invited_by))

    invitation.status = INVITE_ACCEPTED
    invitation.accepted_at = datetime.now(UTC)
    invitation.accepted_user_id = user.id
    # Consumed: single use, enforced by replacing the hash.
    invitation.token_hash = invites.hash_token(f"accepted-{uuid.uuid4().hex}")

    db.flush()
    record_admin_event(
        db,
        # The actor is the person who just accepted, not the inviter: this row
        # records that *they* used the link.
        admin_user=user,
        event_type=event_types.ADMIN_INVITATION_ACCEPTED,
        action="Accepted an administrator invitation",
        entity_type="admin_user",
        entity_id=str(user.id),
        metadata={
            "email": user.email,
            "roles": [r.name for r in roles if r.is_active],
            "invitation_id": str(invitation.id),
        },
        commit=False,
        **_request_context(request),
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise _conflict("An account with that email address already exists.") from exc
    db.refresh(user)
    return user
