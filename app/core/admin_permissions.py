from __future__ import annotations

import enum
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User, UserRole


class AdminPermissionLevel(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    SUPPORT = "support"
    FINANCE = "finance"
    INVENTORY = "inventory"
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
            "promotions",
            "reviews",
            "notifications",
            "support",
            "delivery",
            "finance",
            "payouts",
        }
    ),
    AdminPermissionLevel.SUPPORT: frozenset(
        {
            "dashboard",
            "audit_log",
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
    return AdminPermissionLevel.ADMIN


def admin_has_feature(user: User, feature: str) -> bool:
    if user.role != UserRole.ADMIN:
        return False
    level = resolve_admin_permission(user)
    allowed = PERMISSION_FEATURES.get(level, frozenset())
    return "*" in allowed or feature in allowed


def require_admin(user: User) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user


def require_admin_feature(feature: str):
    def dependency(
        current_user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        user = require_admin(current_user)
        if not admin_has_feature(user, feature):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
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
    _ = db
    user = require_admin(current_user)
    if not admin_has_feature(user, "audit_log"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Audit log access required.",
        )
    return user
