from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.controllers.campaign_controller import list_public_campaigns
from app.controllers.catalog_controller import (
    list_active_flash_sale_events,
    list_promo_banners,
    serialize_catalog_products,
)
from app.controllers.voucher_controller import list_public_promotions
from app.core.promo_banner_config import PROMO_CAMPAIGN_TAGS
from app.models import Product, Voucher
from app.schemas.catalog import DealsHubRead, DealsHubSectionRead


def _active_deal_products(db: Session, *, limit: int = 24) -> list:
    now = datetime.now(timezone.utc)
    products = list(
        db.scalars(
            select(Product)
            .where(
                Product.is_active.is_(True),
                Product.status == "active",
                Product.stock > 0,
            )
            .order_by(Product.updated_at.desc())
            .limit(limit * 3)
        ).all()
    )

    deal_products = []
    for product in products:
        if product.old_price is not None and product.old_price > product.price:
            if product.sale_starts_at and product.sale_starts_at > now:
                continue
            if product.sale_ends_at and product.sale_ends_at < now:
                continue
            deal_products.append(product)
        if len(deal_products) >= limit:
            break

    return serialize_catalog_products(db, deal_products)


def get_deals_hub(db: Session, user_id: str | None = None) -> DealsHubRead:
    banners = list_promo_banners(db, placement="deals")
    flash_events = list_active_flash_sale_events(db)
    promotions = list_public_promotions(db, user_id=user_id)
    deal_products = _active_deal_products(db)
    campaigns = list_public_campaigns(db, limit=24, user_id=user_id)

    sections: list[DealsHubSectionRead] = []

    if campaigns:
        sections.append(
            DealsHubSectionRead(
                key="merchandising-campaigns",
                title="Campaigns",
                subtitle="Seasonal offers and curated marketplace campaigns",
                kind="campaigns",
                count=len(campaigns),
            )
        )

    if banners:
        sections.append(
            DealsHubSectionRead(
                key="banners",
                title="Featured banners",
                subtitle="Highlighted marketplace creatives",
                kind="banners",
            )
        )

    if flash_events:
        primary = flash_events[0]
        sections.append(
            DealsHubSectionRead(
                key="flash-sales",
                title=primary.title,
                subtitle=primary.subtitle or "Limited-time savings",
                kind="flash_sales",
                badge="Live now",
            )
        )

    if promotions:
        sections.append(
            DealsHubSectionRead(
                key="promo-codes",
                title="Promo codes",
                subtitle="Save codes to your wallet and use them at checkout",
                kind="vouchers",
                count=len(promotions),
            )
        )

    if deal_products:
        sections.append(
            DealsHubSectionRead(
                key="todays-deals",
                title="Today's deals",
                subtitle="Products with active sale pricing",
                kind="products",
                count=len(deal_products),
            )
        )

    for campaign in campaigns:
        sections.append(
            DealsHubSectionRead(
                key=f"campaign:{campaign.slug}",
                title=campaign.title,
                subtitle=campaign.subtitle or "Shop this campaign",
                kind="campaign",
                count=campaign.product_count,
                slug=campaign.slug,
            )
        )

    for tag, label in PROMO_CAMPAIGN_TAGS:
        tagged_count = db.scalar(
            select(func.count())
            .select_from(Voucher)
            .where(
                Voucher.campaign_tag == tag,
                Voucher.is_active.is_(True),
                Voucher.approval_status == "approved",
            )
        )
        if tagged_count and int(tagged_count) > 0:
            sections.append(
                DealsHubSectionRead(
                    key=tag,
                    title=label,
                    subtitle="Curated promo codes for this campaign",
                    kind="campaign_vouchers",
                    count=int(tagged_count),
                )
            )

    return DealsHubRead(
        banners=banners,
        flash_events=flash_events,
        promotions=promotions,
        deal_products=deal_products,
        campaigns=campaigns,
        sections=sections,
        campaign_tags=[{"tag": tag, "label": label} for tag, label in PROMO_CAMPAIGN_TAGS],
    )
