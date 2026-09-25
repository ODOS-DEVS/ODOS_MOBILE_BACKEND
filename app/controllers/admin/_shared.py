"""Helpers used by more than one admin domain.

Kept here rather than in any one domain module so the domains do not have to
import from each other, which would reintroduce the tangle the split exists to
remove.
"""


from fastapi import HTTPException, status

from app.models import (
    User,
    UserRole,
)

SUPPORTED_ACCOUNT_STATUSES = {"active", "blocked", "inactive"}
SUPPORTED_VENDOR_STATUSES = {"active", "suspended"}
SUPPORTED_STORE_STATUSES = {"active", "suspended", "draft"}
SUPPORTED_PRODUCT_STATUSES = {"pending", "active", "hidden", "suspended"}
SUPPORTED_ORDER_STATUSES = {
    "pending",
    "confirmed",
    "processing",
    "ready",
    "out_for_delivery",
    "delivered",
    "cancelled",
}


def require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required for this action.",
        )
