"""Analytics for vendor-created promotions (vouchers and campaigns).

All queries are scoped to a specific vendor's store.
Security: Vendor can only see analytics for their own store.
"""

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    PromoAnalyticsEvent,
    Voucher,
    MerchandisingCampaign,
    Store,
)


async def get_vendor_voucher_analytics(
    db: Session,
    vendor_store_id: str,
    days: int = 30,
) -> dict:
    """Get analytics for all vouchers created by this vendor.

    Security: Only returns vouchers belonging to vendor's store.
    """
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Get all vendor's vouchers (with security check)
    vouchers = db.scalars(
        select(Voucher)
        .where(
            Voucher.store_id == vendor_store_id,  # Only their store
            Voucher.owner_type == "vendor",  # Only vendor-owned
        )
    ).all()

    if not vouchers:
        return {
            "total_views": 0,
            "total_clicks": 0,
            "total_conversions": 0,
            "vouchers": [],
        }

    voucher_ids = [v.id for v in vouchers]

    # Get analytics for these vouchers in date range
    events = db.execute(
        select(
            PromoAnalyticsEvent.entity_id,
            PromoAnalyticsEvent.event_type,
            func.count(PromoAnalyticsEvent.id).label("count"),
        )
        .where(
            PromoAnalyticsEvent.entity_type == "voucher",
            PromoAnalyticsEvent.entity_id.in_([str(v_id) for v_id in voucher_ids]),
            PromoAnalyticsEvent.created_at >= start_date,
        )
        .group_by(PromoAnalyticsEvent.entity_id, PromoAnalyticsEvent.event_type)
    ).all()

    # Build voucher-specific stats
    voucher_stats = {}
    total_views = 0
    total_clicks = 0
    total_conversions = 0

    for entity_id, event_type, count in events:
        if entity_id not in voucher_stats:
            voucher_stats[entity_id] = {"impressions": 0, "clicks": 0, "conversions": 0}

        if event_type == "impression":
            voucher_stats[entity_id]["impressions"] = count
            total_views += count
        elif event_type == "click":
            voucher_stats[entity_id]["clicks"] = count
            total_clicks += count
        elif event_type == "conversion":
            voucher_stats[entity_id]["conversions"] = count
            total_conversions += count

    # Build response with voucher details
    voucher_results = []
    for voucher in vouchers:
        stats = voucher_stats.get(str(voucher.id), {"impressions": 0, "clicks": 0, "conversions": 0})
        click_rate = (
            (stats["clicks"] / stats["impressions"] * 100)
            if stats["impressions"] > 0
            else 0
        )
        conversion_rate = (
            (stats["conversions"] / stats["clicks"] * 100)
            if stats["clicks"] > 0
            else 0
        )

        voucher_results.append(
            {
                "id": str(voucher.id),
                "code": voucher.code,
                "title": voucher.title,
                "impressions": stats["impressions"],
                "clicks": stats["clicks"],
                "conversions": stats["conversions"],
                "click_rate": round(click_rate, 1),
                "conversion_rate": round(conversion_rate, 1),
            }
        )

    # Sort by conversions (best performers first)
    voucher_results.sort(key=lambda x: x["conversions"], reverse=True)

    return {
        "total_views": total_views,
        "total_clicks": total_clicks,
        "total_conversions": total_conversions,
        "vouchers": voucher_results,
    }


async def get_vendor_campaign_analytics(
    db: Session,
    vendor_store_id: str,
    days: int = 30,
) -> dict:
    """Get analytics for all campaigns created for this vendor's store.

    Security: Only returns campaigns assigned to vendor's store.
    """
    now = datetime.now(timezone.utc)
    start_date = now - timedelta(days=days)

    # Get store object
    store = db.scalar(select(Store).where(Store.id == vendor_store_id))
    if not store:
        return {
            "total_views": 0,
            "total_clicks": 0,
            "total_conversions": 0,
            "campaigns": [],
        }

    # Get campaigns targeting this vendor's store
    campaigns = db.scalars(
        select(MerchandisingCampaign)
        .join(
            select(MerchandisingCampaign.id)
            .join(
                "merchandising_campaign_stores",
                MerchandisingCampaign.id
                == select(MerchandisingCampaign.id)
                .select_from("merchandising_campaign_stores"),
            )
            .where("merchandising_campaign_stores.store_id" == vendor_store_id)
        )
    ).all()

    # Alternative simpler approach: get campaigns and filter in Python
    all_campaigns = db.scalars(
        select(MerchandisingCampaign).where(MerchandisingCampaign.is_active.is_(True))
    ).all()

    # Import here to avoid circular imports
    from app.services.campaign_service import get_campaign_target_maps

    vendor_campaigns = []
    for campaign in all_campaigns:
        _, _, _, store_ids = get_campaign_target_maps(db, campaign.id)
        if vendor_store_id in store_ids:
            vendor_campaigns.append(campaign)

    if not vendor_campaigns:
        return {
            "total_views": 0,
            "total_clicks": 0,
            "total_conversions": 0,
            "campaigns": [],
        }

    campaign_ids = [str(c.id) for c in vendor_campaigns]

    # Get analytics for these campaigns in date range
    events = db.execute(
        select(
            PromoAnalyticsEvent.entity_id,
            PromoAnalyticsEvent.event_type,
            func.count(PromoAnalyticsEvent.id).label("count"),
        )
        .where(
            PromoAnalyticsEvent.entity_type == "campaign",
            PromoAnalyticsEvent.entity_id.in_(campaign_ids),
            PromoAnalyticsEvent.created_at >= start_date,
        )
        .group_by(PromoAnalyticsEvent.entity_id, PromoAnalyticsEvent.event_type)
    ).all()

    # Build campaign-specific stats
    campaign_stats = {}
    total_views = 0
    total_clicks = 0
    total_conversions = 0

    for entity_id, event_type, count in events:
        if entity_id not in campaign_stats:
            campaign_stats[entity_id] = {"impressions": 0, "clicks": 0, "conversions": 0}

        if event_type == "impression":
            campaign_stats[entity_id]["impressions"] = count
            total_views += count
        elif event_type == "click":
            campaign_stats[entity_id]["clicks"] = count
            total_clicks += count
        elif event_type == "conversion":
            campaign_stats[entity_id]["conversions"] = count
            total_conversions += count

    # Build response with campaign details
    campaign_results = []
    for campaign in vendor_campaigns:
        stats = campaign_stats.get(str(campaign.id), {"impressions": 0, "clicks": 0, "conversions": 0})
        click_rate = (
            (stats["clicks"] / stats["impressions"] * 100)
            if stats["impressions"] > 0
            else 0
        )
        conversion_rate = (
            (stats["conversions"] / stats["clicks"] * 100)
            if stats["clicks"] > 0
            else 0
        )

        campaign_results.append(
            {
                "id": str(campaign.id),
                "slug": campaign.slug,
                "title": campaign.title,
                "impressions": stats["impressions"],
                "clicks": stats["clicks"],
                "conversions": stats["conversions"],
                "click_rate": round(click_rate, 1),
                "conversion_rate": round(conversion_rate, 1),
            }
        )

    # Sort by conversions (best performers first)
    campaign_results.sort(key=lambda x: x["conversions"], reverse=True)

    return {
        "total_views": total_views,
        "total_clicks": total_clicks,
        "total_conversions": total_conversions,
        "campaigns": campaign_results,
    }
