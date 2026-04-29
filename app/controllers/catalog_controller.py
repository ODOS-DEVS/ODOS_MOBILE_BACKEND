from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.models import Category, Market, Product, Store


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
    limit: int | None = None,
) -> list[Product]:
    statement: Select[tuple[Product]] = select(Product).where(Product.is_active.is_(True))

    if audience:
        statement = statement.where(Product.audience_slug == audience)

    if section:
        statement = statement.where(Product.section == section)

    statement = statement.order_by(Product.sort_order.asc(), Product.title.asc())

    if limit is not None:
        statement = statement.limit(limit)

    return list(db.scalars(statement).all())


def get_catalog_product(db: Session, product_id: str) -> Product | None:
    return db.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.is_active.is_(True),
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
) -> list[Store]:
    statement: Select[tuple[Store]] = select(Store).where(Store.is_active.is_(True))

    if market_slug:
        statement = statement.where(Store.market_slug == market_slug)

    if category:
        statement = statement.where(Store.category == category)

    statement = statement.order_by(Store.sort_order.asc(), Store.title.asc())

    return list(db.scalars(statement).all())


def get_store(db: Session, store_id: str) -> Store | None:
    return db.scalar(
        select(Store).where(
            Store.id == store_id,
            Store.is_active.is_(True),
        )
    )
