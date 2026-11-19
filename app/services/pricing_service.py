"""Centralized product and cart pricing for ODOS checkout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import FlashSaleEvent, FlashSaleEventProduct, Product
from app.models.catalog import MerchandisingCampaign, MerchandisingCampaignOptIn
from app.schemas.order import OrderItemCreate
from app.services.finance_math import round_money


@dataclass(slots=True)
class EffectiveProductPrice:
    product_id: str
    regular_price: float
    sale_price: float
    compare_at_price: float | None
    is_on_sale: bool
    is_flash_sale: bool
    flash_event_id: str | None
    flash_event_slug: str | None
    flash_event_title: str | None
    flash_sale_ends_at: datetime | None
    flash_stock_limit: int | None
    flash_units_remaining: int | None
    savings_amount: float
    discount_label: str | None
    #: True when a live campaign's approved price is what the shopper pays. Kept distinct from
    #: `is_flash_sale` so a campaign deal is never reported as a flash sale.
    is_campaign_price: bool = False
    campaign_slug: str | None = None
    campaign_title: str | None = None


def _is_within_sale_window(
    *,
    starts_at: datetime | None,
    ends_at: datetime | None,
    now: datetime,
) -> bool:
    if starts_at and starts_at > now:
        return False
    return not (ends_at and ends_at < now)


def _build_discount_label(compare_at: float, sale: float) -> str | None:
    if compare_at <= sale or compare_at <= 0:
        return None
    percent = round(((compare_at - sale) / compare_at) * 100)
    if percent > 0:
        return f"{percent}% off"
    return None


def get_flash_sale_context_map(
    db: Session,
    product_ids: list[str],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, object]]:
    if not product_ids:
        return {}

    current = now or datetime.now(UTC)
    rows = db.execute(
        select(
            FlashSaleEventProduct.product_id,
            FlashSaleEventProduct.flash_sale_price,
            FlashSaleEventProduct.flash_sale_old_price,
            FlashSaleEventProduct.stock_limit,
            FlashSaleEventProduct.units_sold,
            FlashSaleEvent.id,
            FlashSaleEvent.ends_at,
            FlashSaleEvent.slug,
            FlashSaleEvent.title,
        )
        .join(FlashSaleEvent, FlashSaleEvent.id == FlashSaleEventProduct.event_id)
        .where(
            FlashSaleEventProduct.product_id.in_(product_ids),
            FlashSaleEvent.is_active.is_(True),
            FlashSaleEvent.ends_at > current,
        )
        .order_by(FlashSaleEvent.ends_at.asc())
    ).all()

    context: dict[str, dict[str, object]] = {}
    for product_id, flash_price, flash_old_price, stock_limit, units_sold, event_id, ends_at, slug, title in rows:
        if product_id in context:
            continue
        # A stock-limited flash item that has sold through its allocation is no
        # longer a flash deal — fall back to regular pricing for it rather than
        # exposing a price nobody can actually get.
        if stock_limit is not None and units_sold >= stock_limit:
            continue
        remaining = (stock_limit - units_sold) if stock_limit is not None else None
        context[product_id] = {
            "flash_sale_price": flash_price,
            "flash_sale_old_price": flash_old_price,
            "event_id": str(event_id),
            "ends_at": ends_at,
            "slug": slug,
            "title": title,
            "stock_limit": stock_limit,
            "units_remaining": remaining,
        }
    return context


def get_campaign_price_map(
    db: Session,
    product_ids: list[str],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, object]]:
    """The approved campaign price for each product, from campaigns running right now.

    A campaign price is a real price, not a label on one page. A shopper shown 350 on the
    campaign page and charged 450 at checkout has been misled, so this has to reach the same
    resolver the cart uses rather than being painted on at the end.

    Only approved nominations in live campaigns count. The window is checked in SQL rather
    than through `campaign_service.campaign_is_live` to keep this module free of a controller
    import, and the conditions are the same ones that decide whether the campaign is public:
    active, public, out of draft, started, not ended.

    Where a product is approved in two overlapping campaigns the cheapest wins — the shopper
    gets the better of two offers they were genuinely shown.
    """
    if not product_ids:
        return {}

    current = now or datetime.now(UTC)
    rows = db.execute(
        select(
            MerchandisingCampaignOptIn.product_id,
            MerchandisingCampaignOptIn.proposed_price,
            MerchandisingCampaignOptIn.proposed_old_price,
            MerchandisingCampaign.slug,
            MerchandisingCampaign.title,
            MerchandisingCampaign.ends_at,
        )
        .join(
            MerchandisingCampaign,
            MerchandisingCampaign.id == MerchandisingCampaignOptIn.campaign_id,
        )
        .where(
            MerchandisingCampaignOptIn.product_id.in_(product_ids),
            MerchandisingCampaignOptIn.status == "approved",
            MerchandisingCampaignOptIn.proposed_price.is_not(None),
            MerchandisingCampaign.is_active.is_(True),
            MerchandisingCampaign.visibility == "public",
            MerchandisingCampaign.status.notin_(["draft", "archived", "ended"]),
            or_(
                MerchandisingCampaign.starts_at.is_(None),
                MerchandisingCampaign.starts_at <= current,
            ),
            or_(
                MerchandisingCampaign.ends_at.is_(None),
                MerchandisingCampaign.ends_at >= current,
            ),
        )
    ).all()

    best: dict[str, dict[str, object]] = {}
    for product_id, price, old_price, slug, title, ends_at in rows:
        if price is None:
            continue
        existing = best.get(product_id)
        if existing is not None and float(existing["campaign_price"]) <= float(price):
            continue
        best[product_id] = {
            "campaign_price": float(price),
            "campaign_old_price": float(old_price) if old_price is not None else None,
            "campaign_slug": slug,
            "campaign_title": title,
            "campaign_ends_at": ends_at,
        }
    return best


def resolve_effective_product_price(
    product: Product,
    *,
    flash_context: dict[str, object] | None = None,
    campaign_context: dict[str, object] | None = None,
    now: datetime | None = None,
) -> EffectiveProductPrice:
    current = now or datetime.now(UTC)
    catalog_price = float(product.price)
    regular = float(product.regular_price if product.regular_price is not None else product.price)
    compare_at = float(product.old_price) if product.old_price is not None else None

    flash_price = None
    flash_old_price = None
    flash_ends_at = None
    flash_event_id = None
    flash_slug = None
    flash_title = None
    flash_stock_limit = None
    flash_units_remaining = None
    is_flash_sale = False

    if flash_context:
        raw_flash_price = flash_context.get("flash_sale_price")
        if raw_flash_price is not None:
            flash_price = float(raw_flash_price)
            flash_old_price = (
                float(flash_context["flash_sale_old_price"])
                if flash_context.get("flash_sale_old_price") is not None
                else compare_at or regular
            )
            flash_ends_at = flash_context.get("ends_at")  # type: ignore[assignment]
            flash_event_id = flash_context.get("event_id")  # type: ignore[assignment]
            flash_slug = str(flash_context.get("slug") or "")
            flash_title = str(flash_context.get("title") or "")
            flash_stock_limit = flash_context.get("stock_limit")  # type: ignore[assignment]
            flash_units_remaining = flash_context.get("units_remaining")  # type: ignore[assignment]
            is_flash_sale = True
            catalog_price = flash_price
            compare_at = flash_old_price
            regular = flash_old_price

    # Campaign pricing. Applied after flash so the two can coexist: whichever is genuinely
    # cheaper is what the shopper pays, because both were advertised to them.
    is_campaign_price = False
    campaign_slug = None
    campaign_title = None
    if campaign_context:
        raw_campaign_price = campaign_context.get("campaign_price")
        if raw_campaign_price is not None:
            campaign_price = float(raw_campaign_price)
            if campaign_price < catalog_price:
                reference = compare_at or regular or catalog_price
                catalog_price = campaign_price
                compare_at = max(reference, campaign_price) if reference else None
                is_campaign_price = True
                campaign_slug = str(campaign_context.get("campaign_slug") or "") or None
                campaign_title = str(campaign_context.get("campaign_title") or "") or None

    sale_window_active = _is_within_sale_window(
        starts_at=product.sale_starts_at,
        ends_at=product.sale_ends_at,
        now=current,
    )
    is_on_sale = False
    if not is_flash_sale and sale_window_active:
        if compare_at is not None and compare_at > catalog_price:
            is_on_sale = True
        elif product.old_price is not None and float(product.old_price) > catalog_price:
            is_on_sale = True
            compare_at = float(product.old_price)
    elif not is_flash_sale and compare_at is not None and compare_at > catalog_price:
        is_on_sale = True

    savings = 0.0
    if compare_at is not None and compare_at > catalog_price:
        savings = round_money(compare_at - catalog_price)

    # The stored label describes the shelf price against its own RRP. Once a flash sale or a
    # campaign has replaced the price, that sentence is about numbers the shopper is no longer
    # being offered — a product reduced to 180 from 260 was still advertising "4% off" — so an
    # overridden price always gets a freshly computed label.
    if is_flash_sale or is_campaign_price:
        label = _build_discount_label(compare_at, catalog_price) if compare_at is not None else None
    else:
        label = product.discount
        if not label and compare_at is not None:
            label = _build_discount_label(compare_at, catalog_price)

    return EffectiveProductPrice(
        product_id=product.id,
        regular_price=regular,
        sale_price=catalog_price,
        compare_at_price=compare_at,
        is_on_sale=is_on_sale or is_flash_sale,
        is_flash_sale=is_flash_sale,
        flash_event_id=flash_event_id,  # type: ignore[arg-type]
        flash_event_slug=flash_slug,
        flash_event_title=flash_title,
        flash_sale_ends_at=flash_ends_at,  # type: ignore[arg-type]
        flash_stock_limit=flash_stock_limit,  # type: ignore[arg-type]
        flash_units_remaining=flash_units_remaining,  # type: ignore[arg-type]
        savings_amount=savings,
        discount_label=label,
        is_campaign_price=is_campaign_price,
        campaign_slug=campaign_slug,
        campaign_title=campaign_title,
    )


def resolve_cart_line_prices(
    db: Session,
    items: list[OrderItemCreate],
) -> dict[str, EffectiveProductPrice]:
    product_ids = [item.product_id for item in items]
    if not product_ids:
        return {}

    products = {
        product.id: product
        for product in db.scalars(select(Product).where(Product.id.in_(product_ids))).all()
    }
    flash_map = get_flash_sale_context_map(db, product_ids)
    # The cart has to price a campaign product the same way the campaign page showed it.
    campaign_map = get_campaign_price_map(db, product_ids)
    now = datetime.now(UTC)

    resolved: dict[str, EffectiveProductPrice] = {}
    for product_id in product_ids:
        product = products.get(product_id)
        if not product:
            continue
        resolved[product_id] = resolve_effective_product_price(
            product,
            flash_context=flash_map.get(product_id),
            campaign_context=campaign_map.get(product_id),
            now=now,
        )
    return resolved


def compute_server_subtotal(
    db: Session,
    items: list[OrderItemCreate],
) -> tuple[float, dict[str, EffectiveProductPrice]]:
    resolved = resolve_cart_line_prices(db, items)
    subtotal = 0.0
    for item in items:
        pricing = resolved.get(item.product_id)
        if not pricing:
            raise ValueError(
                f"Product {item.product_id} is no longer available for checkout."
            )
        subtotal += pricing.sale_price * item.quantity
    return round_money(subtotal), resolved
