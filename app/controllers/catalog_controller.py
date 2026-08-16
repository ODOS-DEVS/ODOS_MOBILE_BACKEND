from datetime import datetime, timedelta, timezone
from typing import TypedDict

from sqlalchemy import Select, false, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Category,
    FlashSaleEvent,
    FlashSaleEventProduct,
    Market,
    Product,
    PromoBanner,
    Store,
)
from app.schemas.catalog import FlashSaleEventRead, ProductRead
from app.services.pricing_service import get_flash_sale_context_map, resolve_effective_product_price


class FlashProductContext(TypedDict):
    ends_at: datetime
    slug: str
    title: str


def _normalize_filter_value(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower().strip())
    return "-".join(segment for segment in cleaned.split("-") if segment)


def _normalized_column_value(column):
    return func.btrim(
        func.regexp_replace(func.lower(func.coalesce(column, "")), r"[^a-z0-9]+", "-", "g"),
        "-",
    )


def _find_category_for_filter(db: Session, category: str) -> Category | None:
    cleaned = category.strip()
    if not cleaned:
        return None

    normalized = _normalize_filter_value(cleaned)
    slug_candidates = {cleaned, normalized}

    for slug in slug_candidates:
        if not slug:
            continue
        match = db.scalar(
            select(Category).where(
                Category.slug == slug,
                Category.is_active.is_(True),
            )
        )
        if match:
            return match

    for entry in list_catalog_categories(db):
        if entry.slug in slug_candidates:
            return entry
        if _normalize_filter_value(entry.slug) in slug_candidates:
            return entry
        if _normalize_filter_value(entry.title) == normalized:
            return entry
        if entry.title.strip().lower() == cleaned.lower():
            return entry

    return None


def _category_slug_variants(category: str, category_row: Category | None) -> list[str]:
    variants: set[str] = set()
    cleaned = category.strip()
    normalized = _normalize_filter_value(cleaned)

    if cleaned:
        variants.add(cleaned)
    if normalized:
        variants.add(normalized)

    if category_row:
        variants.add(category_row.slug)
        variants.add(_normalize_filter_value(category_row.slug))
        variants.add(_normalize_filter_value(category_row.title))

    return [value for value in variants if value]


def _build_category_match_filters(
    db: Session,
    category: str,
) -> tuple[list, bool, list[str]]:
    """Match products linked via category_slugs, category label, or audience (Gents/Ladies/Kids)."""
    category_row = _find_category_for_filter(db, category)
    slug_variants = _category_slug_variants(category, category_row)
    filters = []

    if slug_variants:
        filters.append(Product.category_slugs.overlap(slug_variants))
        for slug in slug_variants:
            filters.append(Product.category_slugs.contains([slug]))
            filters.append(Product.audience_slug == slug)

    if category_row:
        title_normalized = _normalize_filter_value(category_row.title)
        filters.append(func.lower(Product.category) == category_row.title.strip().lower())
        filters.append(_normalized_column_value(Product.category) == title_normalized)
        filters.append(Product.category_slugs.contains([category_row.slug]))

    normalized = _normalize_filter_value(category)
    if normalized:
        filters.append(_normalized_column_value(Product.category) == normalized)

    needs_store_join = bool(slug_variants)
    return filters, needs_store_join, slug_variants


def list_catalog_categories(db: Session) -> list[Category]:
    return list(
        db.scalars(
            select(Category)
            .where(Category.is_active.is_(True))
            .order_by(Category.sort_order.asc(), Category.title.asc())
        ).all()
    )


def list_catalog_products(
    db: Session,
    *,
    audience: str | None = None,
    section: str | None = None,
    placement: str | None = None,
    flash_event: str | None = None,
    category: str | None = None,
    subcategory: str | None = None,
    store_id: str | None = None,
    max_age_days: int | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[Product]:
    from app.services.inventory_service import customer_visible_product_filters

    statement: Select[tuple[Product]] = select(Product).where(
        customer_visible_product_filters(),
    )

    if audience:
        normalized_audience = _normalize_filter_value(audience)
        category_row = db.scalar(
            select(Category).where(
                Category.slug == normalized_audience,
                Category.is_active.is_(True),
            )
        )

        audience_filters = [
            Product.audience_slug == audience,
            Product.category_slugs.contains([normalized_audience]),
            _normalized_column_value(Product.category) == normalized_audience,
        ]

        if category_row:
            audience_filters.append(
                _normalized_column_value(Product.category)
                == _normalize_filter_value(category_row.title)
            )
            audience_filters.append(Product.category_slugs.contains([category_row.slug]))

        statement = statement.outerjoin(Store, Product.store_id == Store.id)
        audience_filters.append(Store.audience_slugs.contains([audience]))
        statement = statement.where(or_(*audience_filters)).distinct()

    if section:
        statement = statement.where(Product.section == section)

    if placement:
        if flash_event:
            event_product_ids = _get_flash_event_product_ids(db, flash_event)
            if event_product_ids:
                statement = statement.where(Product.id.in_(event_product_ids))
            else:
                statement = statement.where(false())
        elif placement == "flash-sale":
            live_product_ids = _get_live_flash_event_product_ids(db)
            if live_product_ids:
                statement = statement.where(Product.id.in_(live_product_ids))
            else:
                statement = statement.where(
                    (Product.section == placement)
                    | (Product.section == "flash_sales")
                    | Product.placement_tags.contains([placement])
                )
        else:
            statement = statement.where(
                (Product.section == placement)
                | Product.placement_tags.contains([placement])
            )

    if category:
        category_filters, needs_store_join, slug_variants = _build_category_match_filters(
            db,
            category,
        )
        if needs_store_join:
            statement = statement.outerjoin(Store, Product.store_id == Store.id)
            for slug in slug_variants:
                category_filters.append(Store.audience_slugs.contains([slug]))
        if category_filters:
            statement = statement.where(or_(*category_filters)).distinct()
        else:
            statement = statement.where(false())

    if subcategory:
        normalized_subcategory = _normalize_filter_value(subcategory)
        cleaned_subcategory = subcategory.strip()
        subcategory_filters = [
            Product.subcategory_slugs.contains([normalized_subcategory]),
            _normalized_column_value(Product.subcategory) == normalized_subcategory,
        ]
        if cleaned_subcategory:
            subcategory_filters.append(
                func.lower(Product.subcategory) == cleaned_subcategory.lower()
            )
        statement = statement.where(or_(*subcategory_filters))

    if store_id:
        statement = statement.where(Product.store_id == store_id)

    if max_age_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        statement = statement.where(Product.created_at >= cutoff)

    if placement == "flash-sale":
        statement = statement.order_by(Product.sort_order.asc(), Product.updated_at.desc())
    elif max_age_days is not None:
        statement = statement.order_by(Product.created_at.desc())
    else:
        statement = statement.order_by(Product.sort_order.asc(), Product.title.asc())

    if offset is not None:
        statement = statement.offset(max(offset, 0))

    if limit is not None:
        statement = statement.limit(limit)

    return list(db.scalars(statement).all())


def get_catalog_product(db: Session, product_id: str) -> Product | None:
    from app.services.inventory_service import customer_visible_product_filters

    return db.scalar(
        select(Product).where(
            Product.id == product_id,
            customer_visible_product_filters(),
        )
    )


def list_markets(db: Session) -> list[Market]:
    return list(
        db.scalars(
            select(Market)
            .where(Market.is_active.is_(True))
            .order_by(Market.sort_order.asc(), Market.title.asc())
        ).all()
    )


def list_stores(
    db: Session,
    *,
    market_slug: str | None = None,
    category: str | None = None,
    audience: str | None = None,
) -> list[Store]:
    statement: Select[tuple[Store]] = select(Store).where(
        Store.is_active.is_(True),
        Store.status == "active",
        Store.is_on_vacation.is_(False),
    )

    if market_slug:
        statement = statement.where(Store.market_slug == market_slug)

    if category:
        statement = statement.where(Store.category == category)

    if audience:
        statement = statement.where(Store.audience_slugs.contains([audience]))

    statement = statement.order_by(Store.sort_order.asc(), Store.title.asc())

    return list(db.scalars(statement).all())


def get_store(db: Session, store_id: str) -> Store | None:
    return db.scalar(
        select(Store).where(
            Store.id == store_id,
            Store.is_active.is_(True),
            Store.status == "active",
            Store.is_on_vacation.is_(False),
        )
    )


def list_promo_banners(db: Session, *, placement: str | None = None) -> list[PromoBanner]:
    now = datetime.now(timezone.utc)
    statement = (
        select(PromoBanner)
        .where(PromoBanner.is_active.is_(True))
        .order_by(PromoBanner.sort_order.asc(), PromoBanner.created_at.desc())
    )
    if placement:
        normalized_placement = placement.strip().lower()
        statement = statement.where(PromoBanner.placement == normalized_placement)

    banners = list(db.scalars(statement).all())

    active_banners: list[PromoBanner] = []
    for banner in banners:
        if banner.starts_at and banner.starts_at > now:
            continue
        if banner.ends_at and banner.ends_at < now:
            continue
        active_banners.append(banner)

    return active_banners


def _flash_event_is_live(event: FlashSaleEvent, now: datetime) -> bool:
    if not event.is_active:
        return False
    if event.starts_at and event.starts_at > now:
        return False
    if event.ends_at <= now:
        return False
    return True


def _normalize_event_slug(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower().strip())
    return "-".join(segment for segment in cleaned.split("-") if segment)


def _get_live_flash_events(db: Session, *, slug: str | None = None) -> list[FlashSaleEvent]:
    now = datetime.now(timezone.utc)
    statement = select(FlashSaleEvent).order_by(
        FlashSaleEvent.sort_order.asc(),
        FlashSaleEvent.ends_at.asc(),
    )
    if slug:
        statement = statement.where(FlashSaleEvent.slug == _normalize_event_slug(slug))

    events = list(db.scalars(statement).all())
    return [event for event in events if _flash_event_is_live(event, now)]


def _get_flash_event_product_ids(db: Session, slug: str) -> list[str]:
    events = _get_live_flash_events(db, slug=slug)
    if not events:
        return []

    event = events[0]
    rows = list(
        db.scalars(
            select(FlashSaleEventProduct.product_id)
            .where(FlashSaleEventProduct.event_id == event.id)
            .order_by(FlashSaleEventProduct.sort_order.asc(), FlashSaleEventProduct.product_id.asc())
        ).all()
    )
    return rows


def _get_live_flash_event_product_ids(db: Session) -> list[str]:
    events = _get_live_flash_events(db)
    if not events:
        return []

    event_ids = [event.id for event in events]
    rows = list(
        db.scalars(
            select(FlashSaleEventProduct.product_id)
            .where(FlashSaleEventProduct.event_id.in_(event_ids))
            .order_by(FlashSaleEventProduct.sort_order.asc(), FlashSaleEventProduct.product_id.asc())
        ).all()
    )

    seen: set[str] = set()
    ordered: list[str] = []
    for product_id in rows:
        if product_id in seen:
            continue
        seen.add(product_id)
        ordered.append(product_id)
    return ordered


def build_flash_product_context_map(
    db: Session,
    product_ids: list[str],
) -> dict[str, FlashProductContext]:
    if not product_ids:
        return {}

    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(
            FlashSaleEventProduct.product_id,
            FlashSaleEvent.ends_at,
            FlashSaleEvent.slug,
            FlashSaleEvent.title,
        )
        .join(FlashSaleEvent, FlashSaleEvent.id == FlashSaleEventProduct.event_id)
        .where(
            FlashSaleEventProduct.product_id.in_(product_ids),
            FlashSaleEvent.is_active.is_(True),
            FlashSaleEvent.ends_at > now,
            or_(FlashSaleEvent.starts_at.is_(None), FlashSaleEvent.starts_at <= now),
        )
        .order_by(FlashSaleEvent.ends_at.asc())
    ).all()

    context_map: dict[str, FlashProductContext] = {}
    for product_id, ends_at, slug, title in rows:
        if product_id in context_map:
            continue
        context_map[product_id] = {
            "ends_at": ends_at,
            "slug": slug,
            "title": title,
        }
    return context_map


def serialize_catalog_products(db: Session, products: list[Product]) -> list[ProductRead]:
    if not products:
        return []

    product_ids = [product.id for product in products]
    flash_map = get_flash_sale_context_map(db, product_ids)
    now = datetime.now(timezone.utc)
    serialized: list[ProductRead] = []
    for product in products:
        pricing = resolve_effective_product_price(
            product,
            flash_context=flash_map.get(product.id),
            now=now,
        )
        payload = ProductRead.model_validate(product).model_dump()
        payload["price"] = int(round(pricing.sale_price))
        payload["old_price"] = (
            int(round(pricing.compare_at_price))
            if pricing.compare_at_price is not None
            else None
        )
        payload["discount"] = pricing.discount_label
        if pricing.flash_sale_ends_at:
            payload["flash_sale_ends_at"] = pricing.flash_sale_ends_at
            payload["flash_sale_event_slug"] = pricing.flash_event_slug
            payload["flash_sale_event_title"] = pricing.flash_event_title
            payload["flash_sale_stock_limit"] = pricing.flash_stock_limit
            payload["flash_sale_units_remaining"] = pricing.flash_units_remaining
        serialized.append(ProductRead.model_validate(payload))
    return serialized


def serialize_catalog_product(db: Session, product: Product | None) -> ProductRead | None:
    if product is None:
        return None
    return serialize_catalog_products(db, [product])[0]


def list_active_flash_sale_events(db: Session) -> list[FlashSaleEventRead]:
    now = datetime.now(timezone.utc)
    events = _get_live_flash_events(db)
    if not events:
        return []

    counts = dict(
        db.execute(
            select(FlashSaleEventProduct.event_id, func.count())
            .where(FlashSaleEventProduct.event_id.in_([event.id for event in events]))
            .group_by(FlashSaleEventProduct.event_id)
        ).all()
    )

    payload: list[FlashSaleEventRead] = []
    for event in events:
        seconds_remaining = max(int((event.ends_at - now).total_seconds()), 0)
        payload.append(
            FlashSaleEventRead(
                id=event.id,
                slug=event.slug,
                title=event.title,
                subtitle=event.subtitle,
                image_url=event.image_url,
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                sort_order=event.sort_order,
                product_count=int(counts.get(event.id, 0)),
                seconds_remaining=seconds_remaining,
            )
        )
    return payload


def list_flash_sale_event_products(db: Session, slug: str) -> list[ProductRead]:
    product_ids = _get_flash_event_product_ids(db, slug)
    if not product_ids:
        return []

    from app.services.inventory_service import customer_visible_product_filters

    products = list(
        db.scalars(
            select(Product).where(
                Product.id.in_(product_ids),
                customer_visible_product_filters(),
            )
        ).all()
    )
    product_map = {product.id: product for product in products}
    ordered = [product_map[product_id] for product_id in product_ids if product_id in product_map]
    return serialize_catalog_products(db, ordered)
