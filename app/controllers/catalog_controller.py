from sqlalchemy import Select, func, or_, select
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
        statement = statement.where(Product.audience_slug == audience)

    if section:
        statement = statement.where(Product.section == section)

    if placement:
        statement = statement.where(
            (Product.section == placement)
            | Product.placement_tags.contains([placement])
        )

    if category:
        normalized_category = _normalize_filter_value(category)
        statement = statement.where(
            or_(
                Product.category_slugs.contains([normalized_category]),
                _normalized_column_value(Product.category) == normalized_category,
            )
        )

    if subcategory:
        normalized_subcategory = _normalize_filter_value(subcategory)
        statement = statement.where(
            or_(
                Product.subcategory_slugs.contains([normalized_subcategory]),
                _normalized_column_value(Product.subcategory) == normalized_subcategory,
            )
        )

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
