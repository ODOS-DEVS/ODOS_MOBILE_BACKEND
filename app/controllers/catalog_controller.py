from sqlalchemy import Select, false, func, or_, select
from sqlalchemy.orm import Session

from app.models import Category, Market, Product, Store


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
    category: str | None = None,
    subcategory: str | None = None,
    store_id: str | None = None,
    limit: int | None = None,
) -> list[Product]:
    statement: Select[tuple[Product]] = select(Product).where(
        Product.is_active.is_(True),
        Product.status == "active",
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

    if placement == "flash-sale":
        statement = statement.order_by(Product.sort_order.asc(), Product.updated_at.desc())
    else:
        statement = statement.order_by(Product.sort_order.asc(), Product.title.asc())

    if limit is not None:
        statement = statement.limit(limit)

    return list(db.scalars(statement).all())


def get_catalog_product(db: Session, product_id: str) -> Product | None:
    return db.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.is_active.is_(True),
            Product.status == "active",
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
        )
    )
