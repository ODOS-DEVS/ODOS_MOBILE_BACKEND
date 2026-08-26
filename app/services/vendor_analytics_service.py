"""Analytics for vendor-created promotions (vouchers and campaigns).

All queries are scoped to a specific vendor's store.
Security: Vendor can only see analytics for their own store.

The aggregation itself lives in promo_analytics_service so the vendor view and
the admin view cannot report different numbers for the same events; this module
only decides *which* entities a vendor is allowed to see and shapes the reply.
"""

from app.services.promo_analytics_service import (
    EntityPerformance,
    build_performance,
    campaign_ids_for_store,
    voucher_ids_for_store,
)

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MerchandisingCampaign, Voucher


def _funnel_payload(row: EntityPerformance) -> dict:
    return {
        "id": row.entity_id,
        "title": row.label,
        "impressions": row.funnel.impressions,
        "clicks": row.funnel.clicks,
        "conversions": row.funnel.conversions,
        "click_rate": row.funnel.click_through_rate,
        "conversion_rate": row.funnel.conversion_rate,
    }


def _totals(rows: list[EntityPerformance]) -> dict:
    return {
        "total_views": sum(row.funnel.impressions for row in rows),
        "total_clicks": sum(row.funnel.clicks for row in rows),
        "total_conversions": sum(row.funnel.conversions for row in rows),
    }


async def get_vendor_voucher_analytics(
    db: Session,
    vendor_store_id: str,
    days: int = 30,
) -> dict:
    """Get analytics for all vouchers created by this vendor.

    Security: Only returns vouchers belonging to vendor's store.
    """
    voucher_ids = voucher_ids_for_store(db, vendor_store_id)
    rows = build_performance(db, entity_type="voucher", days=days, entity_ids=voucher_ids)

    codes = {
        str(voucher_id): code
        for voucher_id, code in db.execute(
            select(Voucher.id, Voucher.code).where(
                Voucher.store_id == vendor_store_id,
                Voucher.owner_type == "vendor",
            )
        ).all()
    }

    return {
        **_totals(rows),
        # Redemptions are the part a vendor actually cares about — what the
        # promotion cost them — and only vouchers have a real figure for it.
        "total_redemptions": sum(row.redemption_count for row in rows),
        "total_discount_given": round(sum(row.total_discount_amount for row in rows), 2),
        "vouchers": [
            {
                **_funnel_payload(row),
                "code": codes.get(row.entity_id),
                "redemption_count": row.redemption_count,
                "unique_user_count": row.unique_user_count,
                "total_discount_amount": row.total_discount_amount,
            }
            for row in rows
        ],
    }


async def get_vendor_campaign_analytics(
    db: Session,
    vendor_store_id: str,
    days: int = 30,
) -> dict:
    """Get analytics for campaigns featuring this vendor's store.

    Security: Only returns campaigns assigned to vendor's store.
    """
    campaign_ids = campaign_ids_for_store(db, vendor_store_id)
    rows = build_performance(db, entity_type="campaign", days=days, entity_ids=campaign_ids)

    slugs = {
        str(campaign_id): slug
        for campaign_id, slug in db.execute(
            select(MerchandisingCampaign.id, MerchandisingCampaign.slug)
        ).all()
    }

    return {
        **_totals(rows),
        "campaigns": [
            {**_funnel_payload(row), "slug": slugs.get(row.entity_id)} for row in rows
        ],
    }
