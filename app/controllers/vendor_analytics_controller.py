"""Analytics endpoints for vendors.

Security: Every endpoint checks that the vendor owns the store.
Vendors can ONLY see analytics for their own store's promotions.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import User, Store
from app.services.vendor_analytics_service import (
    get_vendor_voucher_analytics,
    get_vendor_campaign_analytics,
)
from sqlalchemy import select


async def _get_vendor_store_id(db: Session, vendor_user: User) -> str:
    """Get the store ID for a vendor user.

    Security: Ensures this is a valid vendor with a store.
    """
    if not vendor_user or vendor_user.role != "vendor":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only vendors can access vendor analytics.",
        )

    # Get vendor's store
    store = db.scalar(select(Store).where(Store.owner_user_id == vendor_user.id))
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vendor store not found.",
        )

    return store.id


async def get_vendor_voucher_analytics_endpoint(
    db: Session,
    vendor_user: User,
    days: int = 30,
) -> dict:
    """Get analytics for vendor's vouchers.

    Shows: Views, Clicks, Conversions per voucher.
    Security: Only shows vouchers from this vendor's store.
    """
    vendor_store_id = await _get_vendor_store_id(db, vendor_user)
    return await get_vendor_voucher_analytics(db, vendor_store_id, days=days)


async def get_vendor_campaign_analytics_endpoint(
    db: Session,
    vendor_user: User,
    days: int = 30,
) -> dict:
    """Get analytics for campaigns targeting vendor's store.

    Shows: Views, Clicks, Conversions per campaign.
    Security: Only shows campaigns assigned to this vendor's store.
    """
    vendor_store_id = await _get_vendor_store_id(db, vendor_user)
    return await get_vendor_campaign_analytics(db, vendor_store_id, days=days)
