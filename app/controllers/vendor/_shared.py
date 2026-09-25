"""Vendor helpers used by more than one domain.

Both are self-contained lookups -- they call nothing else in the controller --
which is what makes them safe to share without dragging the dashboard cluster
along.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Store,
    User,
    UserRole,
    VendorStatus,
)


def require_vendor_access(user: User) -> None:
    if user.role == UserRole.ADMIN:
        return

    if user.vendor_status == VendorStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vendor access is currently suspended for this account.",
        )

    if user.vendor_status != VendorStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your vendor access is not approved yet.",
        )


def get_vendor_store(db: Session, user: User) -> Store | None:
    return db.scalar(
        select(Store).where(Store.vendor_user_id == user.id)
    )
