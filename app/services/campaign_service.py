"""Merchandising campaign resolution (marketing campaigns, not checkout vouchers)."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from app.models.catalog import (
    MerchandisingCampaign,
    MerchandisingCampaignCategory,
    MerchandisingCampaignOptIn,
    MerchandisingCampaignProduct,
    MerchandisingCampaignStore,
    Product,
)

CAMPAIGN_STATUSES = frozenset({"draft", "scheduled", "active", "ended", "archived"})
PRODUCT_SORT_MODES = frozenset(
    {"manual", "newest", "price_asc", "price_desc", "rating", "popular"}
)
VISIBILITY_MODES = frozenset({"public", "hidden"})


def slugify_campaign(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return cleaned[:80] or "campaign"


def campaign_is_live(campaign: MerchandisingCampaign, *, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    if not campaign.is_active:
        return False
    if campaign.visibility != "public":
        return False
    if campaign.status in {"draft", "archived", "ended"}:
        return False
    if campaign.starts_at and campaign.starts_at > current:
        return False
    if campaign.ends_at and campaign.ends_at < current:
        return False
    return True


def derive_campaign_status(
    campaign: MerchandisingCampaign,
    *,
    now: datetime | None = None,
) -> str:
    """Compute effective schedule-aware status without mutating the row."""
    if campaign.status == "archived":
        return "archived"
    if campaign.status == "draft" or not campaign.is_active:
        return "draft" if campaign.status == "draft" else campaign.status

    current = now or datetime.now(timezone.utc)
    if campaign.ends_at and campaign.ends_at < current:
        return "ended"
    if campaign.starts_at and campaign.starts_at > current:
        return "scheduled"
    return "active"


def sync_campaign_schedule_status(db: Session, campaign: MerchandisingCampaign) -> MerchandisingCampaign:
    """Persist schedule-derived status for active (non-draft/archived) campaigns."""
    if campaign.status in {"draft", "archived"}:
        return campaign
    derived = derive_campaign_status(campaign)
    if derived != campaign.status:
        campaign.status = derived
        db.add(campaign)
    return campaign


def campaign_is_eligible_for_user(
    db: Session,
    campaign: MerchandisingCampaign,
    user_id: str | None,
) -> bool:
    """Check if a user meets campaign eligibility rules."""
    if user_id is None:
        return True
    eligibility_rules_dict = getattr(campaign, "eligibility_rules", None)
    if not eligibility_rules_dict:
        return True
    from app.services.eligibility_service import parse_eligibility_rules, compute_user_eligibility_stats, evaluate_eligibility
    rules = parse_eligibility_rules(eligibility_rules_dict)
    if not rules:
        return True
    stats = compute_user_eligibility_stats(db, user_id)
    ok, _ = evaluate_eligibility(rules, stats, now=datetime.now(timezone.utc))
    return ok


def list_live_campaigns(
    db: Session,
    *,
    featured_only: bool = False,
    limit: int = 40,
    user_id: str | None = None,
) -> list[MerchandisingCampaign]:
    now = datetime.now(timezone.utc)
    rows = list(
        db.scalars(
            select(MerchandisingCampaign)
            .where(
                MerchandisingCampaign.is_active.is_(True),
                MerchandisingCampaign.visibility == "public",
                MerchandisingCampaign.status.notin_(["draft", "archived"]),
                or_(
                    MerchandisingCampaign.starts_at.is_(None),
                    MerchandisingCampaign.starts_at <= now,
                ),
                or_(
                    MerchandisingCampaign.ends_at.is_(None),
                    MerchandisingCampaign.ends_at >= now,
                ),
            )
            .order_by(
                MerchandisingCampaign.is_featured.desc(),
                MerchandisingCampaign.display_priority.asc(),
                MerchandisingCampaign.updated_at.desc(),
            )
            .limit(limit * 2)
        ).all()
    )

    live: list[MerchandisingCampaign] = []
    for campaign in rows:
        sync_campaign_schedule_status(db, campaign)
        if campaign_is_live(campaign, now=now):
            if featured_only and not campaign.is_featured:
                continue
            if not campaign_is_eligible_for_user(db, campaign, user_id):
                continue
            live.append(campaign)
        if len(live) >= limit:
            break

    if live:
        db.commit()
    return live


def get_campaign_by_slug(db: Session, slug: str) -> MerchandisingCampaign | None:
    cleaned = (slug or "").strip().lower()
    if not cleaned:
        return None
    return db.scalar(
        select(MerchandisingCampaign).where(MerchandisingCampaign.slug == cleaned)
    )


def get_campaign_target_maps(
    db: Session,
    campaign_id: uuid.UUID,
) -> tuple[
    list[MerchandisingCampaignProduct],
    list[str],
    list[str],
    list[str],
]:
    products = list(
        db.scalars(
            select(MerchandisingCampaignProduct)
            .where(MerchandisingCampaignProduct.campaign_id == campaign_id)
            .order_by(
                MerchandisingCampaignProduct.is_pinned.desc(),
                MerchandisingCampaignProduct.sort_order.asc(),
            )
        ).all()
    )
    categories = list(
        db.scalars(
            select(MerchandisingCampaignCategory.category_slug).where(
                MerchandisingCampaignCategory.campaign_id == campaign_id
            )
        ).all()
    )
    stores = list(
        db.scalars(
            select(MerchandisingCampaignStore.store_id).where(
                MerchandisingCampaignStore.campaign_id == campaign_id
            )
        ).all()
    )
    approved_opt_ins = list(
        db.scalars(
            select(MerchandisingCampaignOptIn.product_id).where(
                MerchandisingCampaignOptIn.campaign_id == campaign_id,
                MerchandisingCampaignOptIn.status == "approved",
            )
        ).all()
    )
    return products, categories, stores, approved_opt_ins


def resolve_campaign_products(
    db: Session,
    campaign: MerchandisingCampaign,
    *,
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[Product], int]:
    """
    Dynamically resolve products for a campaign from:
    - pinned / manual product assignments
    - category membership
    - store membership
    - approved vendor opt-ins
    - legacy placement_tags matching the campaign slug
    - optional entire-marketplace inclusion
    """
    from app.services.inventory_service import customer_visible_product_filters

    manual_rows, category_slugs, store_ids, approved_opt_in_ids = get_campaign_target_maps(
        db, campaign.id
    )
    manual_ids = [row.product_id for row in manual_rows]
    pin_order = {
        row.product_id: (0 if row.is_pinned else 1, row.sort_order, index)
        for index, row in enumerate(manual_rows)
    }

    filters = list(customer_visible_product_filters()) if campaign.hide_out_of_stock else [
        Product.is_active.is_(True),
        Product.status == "active",
    ]

    id_candidates: set[str] = set(manual_ids)
    id_candidates.update(approved_opt_in_ids)

    or_clauses = []
    if id_candidates:
        or_clauses.append(Product.id.in_(list(id_candidates)))
    if category_slugs:
        or_clauses.append(Product.category_slugs.overlap(category_slugs))
    if store_ids:
        or_clauses.append(Product.store_id.in_(store_ids))
    # Backward-compatible placement tag membership.
    or_clauses.append(Product.placement_tags.contains([campaign.slug]))

    if campaign.include_entire_marketplace:
        query = select(Product).where(*filters)
    elif or_clauses:
        query = select(Product).where(and_(*filters, or_(*or_clauses)))
    else:
        return [], 0

    products = list(db.scalars(query.limit(2000)).all())
    total = len(products)

    sort_mode = campaign.product_sort if campaign.product_sort in PRODUCT_SORT_MODES else "manual"

    def sort_key(product: Product):
        pinned_rank = pin_order.get(product.id, (2, 9999, 9999))
        if sort_mode == "newest":
            return (pinned_rank[0], -(product.created_at.timestamp() if product.created_at else 0))
        if sort_mode == "price_asc":
            return (pinned_rank[0], product.price, product.id)
        if sort_mode == "price_desc":
            return (pinned_rank[0], -product.price, product.id)
        if sort_mode == "rating":
            return (pinned_rank[0], -(product.rating or 0), product.id)
        if sort_mode == "popular":
            return (pinned_rank[0], product.sort_order, product.id)
        # manual: pinned + explicit order, then updated_at
        return (
            pinned_rank[0],
            pinned_rank[1],
            pinned_rank[2],
            -(product.updated_at.timestamp() if product.updated_at else 0),
        )

    products.sort(key=sort_key)

    max_cap = campaign.max_products if campaign.max_products and campaign.max_products > 0 else None
    if max_cap is not None:
        products = products[:max_cap]
        total = len(products)

    page = products[offset : offset + limit]
    return page, total


def replace_campaign_targets(
    db: Session,
    campaign: MerchandisingCampaign,
    *,
    product_ids: list[str] | None = None,
    pinned_product_ids: list[str] | None = None,
    category_slugs: list[str] | None = None,
    store_ids: list[str] | None = None,
) -> None:
    if product_ids is not None:
        db.execute(
            delete(MerchandisingCampaignProduct).where(
                MerchandisingCampaignProduct.campaign_id == campaign.id
            )
        )
        pinned = {pid for pid in (pinned_product_ids or []) if pid}
        for index, product_id in enumerate(product_ids):
            cleaned = (product_id or "").strip()
            if not cleaned:
                continue
            db.add(
                MerchandisingCampaignProduct(
                    campaign_id=campaign.id,
                    product_id=cleaned,
                    sort_order=index,
                    is_pinned=cleaned in pinned,
                )
            )

    if category_slugs is not None:
        db.execute(
            delete(MerchandisingCampaignCategory).where(
                MerchandisingCampaignCategory.campaign_id == campaign.id
            )
        )
        for slug in category_slugs:
            cleaned = (slug or "").strip().lower()
            if cleaned:
                db.add(
                    MerchandisingCampaignCategory(
                        campaign_id=campaign.id,
                        category_slug=cleaned,
                    )
                )

    if store_ids is not None:
        db.execute(
            delete(MerchandisingCampaignStore).where(
                MerchandisingCampaignStore.campaign_id == campaign.id
            )
        )
        for store_id in store_ids:
            cleaned = (store_id or "").strip()
            if cleaned:
                db.add(
                    MerchandisingCampaignStore(
                        campaign_id=campaign.id,
                        store_id=cleaned,
                    )
                )


def count_campaign_products(db: Session, campaign: MerchandisingCampaign) -> int:
    _, total = resolve_campaign_products(db, campaign, limit=1, offset=0)
    return total
