"""Resolving what an administrator is actually allowed to do.

The rule, stated once
---------------------
    effective = (union of permissions on every active role held)
                + every `grant` override
                - every `deny` override

A `deny` override beats a grant from any role, and beats a `grant` override on
the same code. That direction is deliberate: a deny is the thing someone reaches
for when access has to stop *now* — an investigation, a handover, a mistake
being contained — and a rule where the broader grant wins would make it useless
exactly when it matters. It also means revoking access never requires auditing
which of someone's roles happened to include the permission.

Super Admin
-----------
Super Admin is the `users.admin_permission == "super_admin"` flag, not a role.
It short-circuits every check. Keeping it outside the role tables means no role
edit, no override and no accidental un-assignment can lock the platform's owner
out, and the existing account keeps working with no migration.

Caching
-------
Nothing is cached. A permission change has to bite on the administrator's very
next request — the brief is explicit that stale frontend state must not decide
access, and the same applies to a server-side cache. Resolution is two indexed
queries against small tables.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.admin_permission_catalogue import ALL_CODES, PERMISSIONS_BY_CODE
from app.models import User, UserRole
from app.models.admin_rbac import (
    OVERRIDE_DENY,
    OVERRIDE_GRANT,
    AdminPermissionOverride,
    AdminRole,
    AdminRolePermission,
    AdminUserRole,
)

#: What a super admin holds: everything, including anything added later.
SUPER_ADMIN_FLAG = "super_admin"

#: Sentinel written to `users.admin_permission` once an administrator's access
#: is decided by roles.
#:
#: Without it, "remove every role" did not mean "revoke access": an
#: administrator with no role rows reads as unmigrated and falls back to
#: whatever band their old column still said, which for a former
#: Platform Administrator is nearly everything. The sentinel makes the
#: migration a fact about the account rather than something inferred from
#: rows that can all be deleted.
#:
#: It is deliberately not a valid `AdminPermissionLevel`, so if the band path is
#: ever reached for one of these accounts, `resolve_admin_permission` falls to
#: its least-privilege default instead of granting anything.
RBAC_MANAGED_FLAG = "rbac"


def is_super_admin(user: User | None) -> bool:
    if user is None or user.role != UserRole.ADMIN:
        return False
    return (getattr(user, "admin_permission", None) or "").strip().lower() == SUPER_ADMIN_FLAG


def role_permission_codes(db: Session, role_id) -> set[str]:
    """Codes a single role grants, ignoring any that no longer exist in code."""
    rows = db.scalars(
        select(AdminRolePermission.permission_code).where(AdminRolePermission.role_id == role_id)
    ).all()
    return {code for code in rows if code in ALL_CODES}


def is_rbac_managed(db: Session, user: User | None) -> bool:
    """Whether this administrator's access is decided by roles yet.

    Decided on the *existence of assignment rows*, never on whether those rows
    resolve to any permission. The difference is a security hole: an
    administrator whose roles were all removed in order to revoke their access
    resolves to zero permissions, and treating that as "not managed yet" would
    quietly hand them their old permission band back. An administrator with an
    assignment row pointing at an empty or deactivated role is managed, and the
    answer is no.

    An override alone also counts, so denying a single permission to someone who
    holds no roles does not read as unmanaged.
    """
    if user is None or user.role != UserRole.ADMIN:
        return False
    if (getattr(user, "admin_permission", None) or "").strip().lower() == RBAC_MANAGED_FLAG:
        return True
    has_role = db.scalar(
        select(AdminUserRole.id).where(AdminUserRole.user_id == user.id).limit(1)
    )
    if has_role is not None:
        return True
    has_override = db.scalar(
        select(AdminPermissionOverride.id)
        .where(AdminPermissionOverride.user_id == user.id)
        .limit(1)
    )
    return has_override is not None


def _band_permissions(user: User) -> frozenset[str]:
    """What the old module-level band amounts to in per-action terms.

    Needed so `require_permission` can be deployed before the bootstrap has
    run. A band granted whole modules, so it is read as every permission in
    those modules — which is precisely what it allowed. Without this, adding an
    action-level guard to an existing route would 403 every administrator still
    on a band, which is a lockout rather than a tightening.

    This path disappears once every administrator is role-governed.
    """
    from app.core.admin_permission_catalogue import PERMISSIONS_BY_MODULE
    from app.core.admin_permissions import PERMISSION_FEATURES, resolve_admin_permission

    allowed = PERMISSION_FEATURES.get(resolve_admin_permission(user), frozenset())
    if "*" in allowed:
        return frozenset(ALL_CODES)
    codes: set[str] = set()
    for module in allowed:
        codes.update(p.code for p in PERMISSIONS_BY_MODULE.get(module, ()))
    return frozenset(codes)


def effective_permissions(db: Session, user: User | None) -> frozenset[str]:
    """Every permission code this administrator holds right now.

    Returns the whole catalogue for a super admin, and nothing at all for a
    non-admin — so a vendor or shopper token can never satisfy an admin check
    even if it somehow reaches one.

    An administrator who is not yet role-governed is answered from their old
    band, for the reason given in `_band_permissions`.
    """
    if user is None or user.role != UserRole.ADMIN:
        return frozenset()
    if is_super_admin(user):
        return frozenset(ALL_CODES)
    if not is_rbac_managed(db, user):
        return _band_permissions(user)

    # Roles, skipping deactivated ones: switching a role off has to remove the
    # access it carried without unpicking every assignment.
    granted = set(
        db.scalars(
            select(AdminRolePermission.permission_code)
            .join(AdminRole, AdminRole.id == AdminRolePermission.role_id)
            .join(AdminUserRole, AdminUserRole.role_id == AdminRole.id)
            .where(AdminUserRole.user_id == user.id, AdminRole.is_active.is_(True))
        ).all()
    )

    overrides = db.scalars(
        select(AdminPermissionOverride).where(AdminPermissionOverride.user_id == user.id)
    ).all()
    denied: set[str] = set()
    for override in overrides:
        effect = (override.effect or "").strip().lower()
        if effect == OVERRIDE_GRANT:
            granted.add(override.permission_code)
        elif effect == OVERRIDE_DENY:
            denied.add(override.permission_code)

    # Unknown codes are dropped rather than trusted: a permission removed from
    # the catalogue must stop granting anything, not keep granting whatever the
    # string used to mean.
    return frozenset((granted - denied) & ALL_CODES)


def has_permission(db: Session, user: User | None, code: str) -> bool:
    """Whether this administrator may perform one specific action."""
    if code not in PERMISSIONS_BY_CODE:
        # An unknown code is a programming error. Fail closed rather than let a
        # typo in a guard silently authorise everyone.
        return False
    if is_super_admin(user):
        return True
    return code in effective_permissions(db, user)


def has_any_permission(db: Session, user: User | None, codes: list[str] | tuple[str, ...]) -> bool:
    if is_super_admin(user):
        return True
    held = effective_permissions(db, user)
    return any(code in held for code in codes)


def module_access(db: Session, user: User | None) -> frozenset[str]:
    """Modules the administrator holds at least one permission in.

    This is what the coarse, module-level `admin_has_feature` now answers from,
    and what the frontend uses to decide which navigation items exist. Holding
    any permission in a module is what makes the module worth showing; whether
    each individual button appears is a separate, finer question.
    """
    if is_super_admin(user):
        from app.core.admin_permission_catalogue import MODULES

        return frozenset(MODULES)
    return frozenset(
        PERMISSIONS_BY_CODE[code].module for code in effective_permissions(db, user)
    )


def permission_sources(db: Session, user: User) -> dict[str, list[str]]:
    """Which role (or override) is responsible for each held permission.

    Powers the "effective permissions" view: the Super Admin editing someone's
    access needs to see that `vendors.approve` arrives via Vendor Manager, not
    merely that it is ticked — otherwise removing it looks like it failed.
    """
    sources: dict[str, list[str]] = {}
    rows = db.execute(
        select(AdminRole.name, AdminRolePermission.permission_code)
        .join(AdminRolePermission, AdminRolePermission.role_id == AdminRole.id)
        .join(AdminUserRole, AdminUserRole.role_id == AdminRole.id)
        .where(AdminUserRole.user_id == user.id, AdminRole.is_active.is_(True))
    ).all()
    for role_name, code in rows:
        sources.setdefault(code, []).append(role_name)

    for override in db.scalars(
        select(AdminPermissionOverride).where(AdminPermissionOverride.user_id == user.id)
    ).all():
        effect = (override.effect or "").strip().lower()
        label = "Granted directly" if effect == OVERRIDE_GRANT else "Denied directly"
        sources.setdefault(override.permission_code, []).append(label)

    return sources


def count_active_super_admins(db: Session, *, excluding=None) -> int:
    """How many usable super admins exist.

    The guard behind "you cannot deactivate the last one". Counts only active
    accounts, because an inactive super admin cannot sign in to fix anything.
    """
    query = select(User).where(
        User.role == UserRole.ADMIN,
        User.admin_permission == SUPER_ADMIN_FLAG,
        User.is_active.is_(True),
    )
    if excluding is not None:
        query = query.where(User.id != excluding)
    return len(list(db.scalars(query).all()))
