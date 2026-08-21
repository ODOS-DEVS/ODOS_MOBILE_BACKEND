"""Vendor analytics endpoints.

Vendors can see how their promotions are performing.
All data is scoped to their store only.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.controllers.vendor_analytics_controller import (
    get_vendor_voucher_analytics_endpoint,
    get_vendor_campaign_analytics_endpoint,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User

router = APIRouter(prefix="/vendor/analytics", tags=["vendor-analytics"])


@router.get("/vouchers")
async def get_voucher_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    days: int = Query(30, ge=7, le=90, description="Days to look back"),
):
    """Get analytics for your store's vouchers.

    Shows views, clicks, and conversions for each voucher you created.

    **Security:** Only shows your store's vouchers.
    """
    return await get_vendor_voucher_analytics_endpoint(db, current_user, days=days)


@router.get("/campaigns")
async def get_campaign_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    days: int = Query(30, ge=7, le=90, description="Days to look back"),
):
    """Get analytics for campaigns featuring your store.

    Shows views, clicks, and conversions for campaigns that include your store.

    **Security:** Only shows campaigns assigned to your store.
    """
    return await get_vendor_campaign_analytics_endpoint(db, current_user, days=days)
