"""Analytics endpoints for vendors.

Security: Every endpoint checks that the vendor owns the store.
Vendors can ONLY see analytics for their own store's promotions.
"""

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models import User
from app.services.vendor_analytics_service import (
    get_vendor_voucher_analytics,
    get_vendor_campaign_analytics,
)


async def _get_vendor_store_id(db: Session, vendor_user: User) -> str:
    """Get the store ID for a vendor user.

    Security: Ensures this is a valid vendor with a store.

    Both halves defer to vendor_controller rather than re-deriving the rule.
    Doing it locally is what broke this endpoint: it filtered on a
    `Store.owner_user_id` column that does not exist (the column is
    `vendor_user_id`), so every request here raised AttributeError, and it
    rejected an approved vendor whose `role` was still `customer` — which
    `require_vendor_access` accepts and the rest of the vendor API allows.
    """
    from app.controllers.vendor_controller import get_vendor_store, require_vendor_access

    if not vendor_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only vendors can access vendor analytics.",
        )
    require_vendor_access(vendor_user)

    store = get_vendor_store(db, vendor_user)
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


async def get_vendor_promo_overview_endpoint(
    db: Session,
    vendor_user: User,
    days: int = 30,
) -> object:
    """Whole-promotion funnel for this vendor's store.

    Same builder the admin dashboard uses, scoped to one store — so a vendor and
    an admin looking at the same campaign always see the same numbers.
    """
    from app.services.promo_analytics_service import build_overview

    store_id = await _get_vendor_store_id(db, vendor_user)
    return build_overview(db, days=days, store_id=store_id)


async def get_vendor_promo_timeseries_endpoint(
    db: Session,
    vendor_user: User,
    entity_type: str,
    days: int = 30,
    entity_id: str | None = None,
) -> object:
    """Daily funnel for this vendor's campaigns or vouchers."""
    from app.services.promo_analytics_service import (
        build_timeseries,
        campaign_ids_for_store,
        voucher_ids_for_store,
    )

    store_id = await _get_vendor_store_id(db, vendor_user)
    if entity_type == "campaign":
        owned = campaign_ids_for_store(db, store_id)
    elif entity_type == "voucher":
        owned = voucher_ids_for_store(db, store_id)
    else:
        # Banners are marketplace-owned; a vendor has none to report on.
        owned = []

    # A vendor may only chart something they own — otherwise passing an
    # arbitrary entity_id would expose another store's performance.
    if entity_id is not None and entity_id not in owned:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That promotion does not belong to your store.",
        )

    return build_timeseries(
        db,
        entity_type=entity_type,
        days=days,
        entity_ids=owned,
        entity_id=entity_id,
    )


async def get_vendor_promo_leaderboard_endpoint(
    db: Session,
    vendor_user: User,
    entity_type: str,
    days: int = 30,
    limit: int = 10,
) -> object:
    """This vendor's best-performing promotions of the given type."""
    from app.services.promo_analytics_service import (
        build_leaderboard,
        campaign_ids_for_store,
        voucher_ids_for_store,
    )

    store_id = await _get_vendor_store_id(db, vendor_user)
    if entity_type == "campaign":
        owned = campaign_ids_for_store(db, store_id)
    elif entity_type == "voucher":
        owned = voucher_ids_for_store(db, store_id)
    else:
        owned = []

    return build_leaderboard(
        db, entity_type=entity_type, days=days, limit=limit, entity_ids=owned
    )
