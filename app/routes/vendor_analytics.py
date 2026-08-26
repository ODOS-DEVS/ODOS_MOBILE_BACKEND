"""Vendor analytics endpoints.

Vendors can see how their promotions are performing.
All data is scoped to their store only.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.controllers.vendor_analytics_controller import (
    get_vendor_voucher_analytics_endpoint,
    get_vendor_campaign_analytics_endpoint,
    get_vendor_promo_leaderboard_endpoint,
    get_vendor_promo_overview_endpoint,
    get_vendor_promo_timeseries_endpoint,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.promo_analytics import (
    PromoAnalyticsLeaderboardRead,
    PromoAnalyticsOverviewRead,
    PromoAnalyticsTimeseriesRead,
)
from app.services.promo_analytics_service import ENTITY_TYPES

router = APIRouter(prefix="/vendor/analytics", tags=["vendor-analytics"])

# Banners belong to the marketplace, not to any store, so a vendor can only ask
# about the two surfaces they actually own.
VENDOR_ENTITY_TYPES = ("campaign", "voucher")


def _validate_entity_type(entity_type: str) -> None:
    if entity_type not in VENDOR_ENTITY_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"entity_type must be one of: {', '.join(VENDOR_ENTITY_TYPES)}. "
                f"(Marketplace-wide types — {', '.join(ENTITY_TYPES)} — are admin-only.)"
            ),
        )


@router.get("/vouchers")
async def get_voucher_analytics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    days: int = Query(30, ge=7, le=90, description="Days to look back"),
):
    """Get analytics for your store's vouchers.

    Shows views, clicks, conversions, redemptions and discount given for each
    voucher you created.

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


@router.get("/promotions/overview", response_model=PromoAnalyticsOverviewRead)
async def get_promo_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    days: int = Query(30, ge=1, le=365, description="Days to look back"),
):
    """Campaigns and vouchers for your store in one response.

    **Security:** Scoped to your store.
    """
    return await get_vendor_promo_overview_endpoint(db, current_user, days=days)


@router.get("/promotions/timeseries", response_model=PromoAnalyticsTimeseriesRead)
async def get_promo_timeseries(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    entity_type: str = Query("campaign", description="campaign|voucher"),
    entity_id: str | None = Query(None, description="Limit to one of your promotions"),
    days: int = Query(30, ge=1, le=365),
):
    """Daily impressions/clicks/conversions, quiet days included as zeroes.

    **Security:** Scoped to your store; `entity_id` must be one of yours.
    """
    _validate_entity_type(entity_type)
    return await get_vendor_promo_timeseries_endpoint(
        db, current_user, entity_type=entity_type, days=days, entity_id=entity_id
    )


@router.get("/promotions/leaderboard", response_model=PromoAnalyticsLeaderboardRead)
async def get_promo_leaderboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    entity_type: str = Query("campaign", description="campaign|voucher"),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
):
    """Your best-performing promotions of the chosen type.

    **Security:** Scoped to your store.
    """
    _validate_entity_type(entity_type)
    return await get_vendor_promo_leaderboard_endpoint(
        db, current_user, entity_type=entity_type, days=days, limit=limit
    )
