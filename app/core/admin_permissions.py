"""Administrator authorisation.

Two systems live here, on purpose and temporarily.

`PERMISSION_FEATURES` below is the original scheme: one band per administrator,
stored as a string on `users.admin_permission`, granting whole modules. The
role-based system in `app.services.admin_rbac` replaces it with per-action
permissions, multiple roles per person and per-person overrides.

Both are consulted, per administrator, by `admin_has_feature`: an administrator
who has been given roles is answered by RBAC, and one who has not is answered by
their band. That is what makes the rollout safe — this code can deploy before
the bootstrap runs, and nothing changes until it does. The band path is dead
weight once every administrator is on a role, and can be deleted then.

The important subtlety is in `is_rbac_managed`, not here: an administrator whose
roles were all removed must resolve to *no* access, never fall back to their old
band. See that function for why.
"""

from __future__ import annotations

import enum
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.auth import require_admin as auth_require_admin
from app.core.database import get_db
from app.models import User, UserRole


class AdminPermissionLevel(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    SUPPORT = "support"
    FINANCE = "finance"
    INVENTORY = "inventory"
    MARKETING = "marketing"
    MODERATOR = "moderator"
    ANALYST = "analyst"


# Routes/features each band may access. Super admin always passes.
PERMISSION_FEATURES: dict[AdminPermissionLevel, frozenset[str]] = {
    AdminPermissionLevel.SUPER_ADMIN: frozenset({"*"}),
    AdminPermissionLevel.ADMIN: frozenset(
        {
            "dashboard",
            "analytics",
            "audit_log",
            "users",
            "vendors",
            "orders",
            "returns",
            "products",
            "stores",
            "markets",
            "categories",
            "guides",
            "promotions",
            "reviews",
            "notifications",
            "support",
            "delivery",
            "finance",
            "payouts",
            # Added with the "catalog" fix: the services desk was always meant
            # for administrators, not super admins only.
            "services",
        }
    ),
    AdminPermissionLevel.SUPPORT: frozenset(
        {
            "dashboard",
            "audit_log",
            "guides",
            "users",
            "orders",
            "returns",
            "notifications",
            "support",
        }
    ),
    AdminPermissionLevel.FINANCE: frozenset(
        {
            "dashboard",
            "analytics",
            "audit_log",
            "finance",
            "payouts",
            "orders",
        }
    ),
    AdminPermissionLevel.INVENTORY: frozenset(
        {
            "dashboard",
            "audit_log",
            "products",
            "stores",
            "markets",
            "categories",
            "promotions",
        }
    ),
    AdminPermissionLevel.MARKETING: frozenset(
        {
            "dashboard",
            "analytics",
            "audit_log",
            "guides",
            "promotions",
            "notifications",
            "products",
        }
    ),
    AdminPermissionLevel.MODERATOR: frozenset(
        {
            "dashboard",
            "audit_log",
            "products",
            "reviews",
            "vendors",
            "notifications",
            "support",
        }
    ),
    AdminPermissionLevel.ANALYST: frozenset(
        {
            "dashboard",
            "analytics",
            "audit_log",
        }
    ),
}


def resolve_admin_permission(user: User) -> AdminPermissionLevel:
    raw = (getattr(user, "admin_permission", None) or "").strip().lower()
    if raw:
        try:
            return AdminPermissionLevel(raw)
        except ValueError:
            pass
    # Fail closed: unknown / missing band is least privilege for non–super-admins.
    # Legacy admins with null permission keep full "admin" via explicit value or
    # bootstrap which sets super_admin.
    if not raw:
        return AdminPermissionLevel.ADMIN
    return AdminPermissionLevel.ANALYST


def admin_has_feature(user: User, feature: str, db: Session | None = None) -> bool:
    """Whether the administrator may reach anything in `feature`'s module.

    `db` is optional so the handful of callers without a session keep working;
    without one, only the legacy band can be consulted.
    """
    if user.role != UserRole.ADMIN:
        return False

    from app.services.admin_rbac import is_super_admin

    if is_super_admin(user):
        return True

    if db is not None:
        from app.services.admin_rbac import is_rbac_managed, module_access

        if is_rbac_managed(db, user):
            # Authoritative. No fallback: see is_rbac_managed.
            return feature in module_access(db, user)

    level = resolve_admin_permission(user)
    allowed = PERMISSION_FEATURES.get(level, frozenset())
    return "*" in allowed or feature in allowed


def list_admins_with_feature(db: Session, feature: str) -> list[User]:
    """Admin users whose permission band covers `feature` — the recipient
    list for feature-scoped alert emails (e.g. only finance-capable admins
    hear about withdrawal requests)."""
    from sqlalchemy import select

    all_admins = list(db.scalars(select(User).where(User.role == UserRole.ADMIN)).all())
    return [admin for admin in all_admins if admin_has_feature(admin, feature, db)]


def require_admin(user: User) -> User:
    """Admin guard that returns the user, for callers that chain off it.

    Delegates the actual check so the rule and its message live in one place;
    this exists only because require_admin_feature and the audit helpers below
    use the return value.
    """
    auth_require_admin(user)
    return user


def require_admin_feature(feature: str):
    """Module-level guard. Unchanged signature, so every existing alias keeps working.

    This is the coarse check: it answers "may this person reach this area of the
    dashboard at all". It is not sufficient on its own for anything that changes
    a record — use `require_permission` for that, which is what makes viewing an
    order different from cancelling one.
    """

    def dependency(
        current_user: Annotated[User, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> User:
        user = require_admin(current_user)
        if not admin_has_feature(user, feature, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return user

    return dependency


def require_permission(*codes: str):
    """Action-level guard: the real security boundary for anything that writes.

    Passing several codes means "any of these is enough", for an endpoint two
    different roles legitimately reach by different routes. Requiring *all* of
    a set has not been needed and is deliberately not offered, because an
    endpoint that needs two unrelated permissions is usually two endpoints.

    Codes are validated against the catalogue at call time rather than silently
    denying: a typo in a guard is a deployment bug, and it should be loud.
    """
    from app.core.admin_permission_catalogue import PERMISSIONS_BY_CODE

    unknown = [code for code in codes if code not in PERMISSIONS_BY_CODE]
    if unknown:
        raise RuntimeError(f"require_permission references unknown codes: {unknown}")

    def dependency(
        current_user: Annotated[User, Depends(get_current_user)],
        db: Annotated[Session, Depends(get_db)],
    ) -> User:
        user = require_admin(current_user)

        from app.services.admin_rbac import has_any_permission

        if not has_any_permission(db, user, list(codes)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                # Deliberately does not name the missing permission: an
                # attacker probing endpoints should not be handed a map of the
                # permission catalogue.
                detail="You do not have permission to perform this action.",
            )
        return user

    return dependency


def require_super_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    user = require_admin(current_user)
    if resolve_admin_permission(user) != AdminPermissionLevel.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required.",
        )
    return user


def require_audit_access(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    user = require_admin(current_user)
    if not admin_has_feature(user, "audit_log", db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Audit log access required.",
        )
    return user
