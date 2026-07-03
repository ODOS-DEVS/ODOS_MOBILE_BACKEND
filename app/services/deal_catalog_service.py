"""Browse products with vendor-approved or flash-sale discounts."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.controllers.catalog_controller import (
    _get_live_flash_event_product_ids,
    serialize_catalog_products,
)
from app.models import Product
from app.services.pricing_service import get_flash_sale_context_map, resolve_effective_product_price


def _discount_percent(compare_at: float, sale_price: float) -> float:
    if compare_at <= sale_price or compare_at <= 0:
        return 0.0
    return ((compare_at - sale_price) / compare_at) * 100.0


def list_deal_products(
    db: Session,
    *,
    min_discount_percent: int | None = None,
    campaign_tag: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> list:
    """Return products on sale where the vendor (or approved flash event) set the discount."""
    now = datetime.now(timezone.utc)
    normalized_campaign = (campaign_tag or "").strip() or None
    min_discount = max(min_discount_percent or 0, 0)
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(offset, 0)

    candidate_map: dict[str, Product] = {}

    base_statement = select(Product).where(
        Product.is_active.is_(True),
        Product.status == "active",
    )
    if normalized_campaign:
        base_statement = base_statement.where(
            Product.placement_tags.contains([normalized_campaign])
        )

    for product in db.scalars(
        base_statement.order_by(Product.updated_at.desc()).limit(240)
    ).all():
        candidate_map[product.id] = product

    for product_id in _get_live_flash_event_product_ids(db):
        if product_id in candidate_map:
            continue
        product = db.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.is_active.is_(True),
                Product.status == "active",
            )
        )
        if not product:
            continue
        if normalized_campaign:
            tags = product.placement_tags or []
            if normalized_campaign not in tags:
                continue
        candidate_map[product.id] = product

    product_ids = list(candidate_map.keys())
    flash_context_map = get_flash_sale_context_map(db, product_ids, now=now)

    scored: list[tuple[float, Product]] = []
    for product in candidate_map.values():
        pricing = resolve_effective_product_price(
            product,
            flash_context=flash_context_map.get(product.id),
            now=now,
        )
        if not pricing.is_on_sale and not pricing.is_flash_sale:
            continue

        compare_at = pricing.compare_at_price
        if compare_at is None:
            continue

        percent_off = _discount_percent(compare_at, pricing.sale_price)
        if percent_off <= 0:
            continue
        if min_discount > 0 and percent_off < min_discount:
            continue

        scored.append((percent_off, product))

    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    page = [product for _, product in scored[safe_offset : safe_offset + safe_limit]]
    return serialize_catalog_products(db, page)
