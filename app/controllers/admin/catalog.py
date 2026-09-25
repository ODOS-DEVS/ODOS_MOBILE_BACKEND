"""Admin catalogue structure: stores, markets and categories -- the taxonomy
that products are filed under.
"""

import uuid

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.controllers.admin._shared import (
    _generate_store_id,
    _infer_image_key,
    _normalize_list,
    _serialize_store_product,
    _slugify,
    _taxonomy_lookup_by_slug,
)
from app.controllers.vendor_controller import (
    broadcast_catalog_store_change,
)
from app.core.admin_pagination import paginate_scalars
from app.core.auth import require_admin
from app.models import (
    Category,
    Market,
    Order,
    Product,
    Store,
    User,
)
from app.schemas.admin import (
    AdminCategoryRead,
    AdminCategoryUpsert,
    AdminMarketRead,
    AdminMarketUpsert,
    AdminStoreDetailRead,
    AdminStoreRead,
    AdminStoreStatsRead,
    AdminStoreStatusUpdate,
    AdminStoreUpsert,
)
from app.schemas.pagination import AdminPageRead
from app.services.media_service import remove_media_file, save_image_upload
from app.services.realtime_service import realtime_manager

SUPPORTED_ACCOUNT_STATUSES = {"active", "blocked", "inactive"}
SUPPORTED_VENDOR_STATUSES = {"active", "suspended"}
SUPPORTED_STORE_STATUSES = {"active", "suspended", "draft"}
SUPPORTED_PRODUCT_STATUSES = {"pending", "active", "hidden", "suspended"}
SUPPORTED_ORDER_STATUSES = {
    "pending",
    "confirmed",
    "processing",
    "ready",
    "out_for_delivery",
    "delivered",
    "cancelled",
}




def _serialize_store(store: Store) -> AdminStoreRead:
    return AdminStoreRead(
        id=store.id,
        vendor_id=str(store.vendor_user_id) if store.vendor_user_id else None,
        name=store.title,
        slug=store.slug,
        description=store.description or "",
        category=store.category or "",
        audience_slugs=store.audience_slugs,
        market_id=store.market_id,
        location=store.address,
        region=store.region or "",
        city=store.city or "",
        banner_image=store.image_banner_url,
        logo_image=store.image_url,
        status=store.status,
        created_at=store.created_at,
    )


def _store_activity_summary(
    db: Session,
    products: list[Product],
) -> AdminStoreStatsRead:
    total_products = len(products)
    active_products = sum(1 for product in products if product.status == "active")
    pending_products = sum(1 for product in products if product.status == "pending")
    hidden_products = sum(1 for product in products if product.status in {"hidden", "suspended"})

    product_ids = {product.id for product in products}
    if not product_ids:
        return AdminStoreStatsRead(
            total_products=total_products,
            active_products=active_products,
            pending_products=pending_products,
            hidden_products=hidden_products,
            total_orders=0,
            total_sales=0.0,
        )

    orders = list(db.scalars(select(Order).options(selectinload(Order.items))).all())
    total_orders = 0
    total_sales = 0.0

    for order in orders:
        matching_items = [item for item in order.items if item.product_id in product_ids]
        if not matching_items:
            continue
        total_orders += 1
        if order.vendor_status in {"confirmed", "processing", "ready", "delivered"}:
            total_sales += sum(item.line_total for item in matching_items)

    return AdminStoreStatsRead(
        total_products=total_products,
        active_products=active_products,
        pending_products=pending_products,
        hidden_products=hidden_products,
        total_orders=total_orders,
        total_sales=round(total_sales, 2),
    )


def _serialize_store_detail(
    db: Session,
    store: Store,
    *,
    vendor: User | None = None,
    market: Market | None = None,
    products: list[Product],
) -> AdminStoreDetailRead:
    base = _serialize_store(store)
    return AdminStoreDetailRead(
        **base.model_dump(),
        vendor_name=vendor.full_name if vendor else None,
        vendor_email=vendor.email if vendor else None,
        vendor_phone_number=vendor.phone_number if vendor else None,
        market_name=market.title if market else None,
        updated_at=store.updated_at,
        products=[_serialize_store_product(product) for product in products],
        stats=_store_activity_summary(db, products),
    )


def _serialize_market(market: Market) -> AdminMarketRead:
    return AdminMarketRead(
        id=market.id,
        name=market.title,
        slug=market.slug,
        image=market.image_key,
        image_url=market.image_url,
        status="active" if market.is_active else "disabled",
        created_at=market.created_at,
    )


def _serialize_category(category: Category) -> AdminCategoryRead:
    return AdminCategoryRead(
        id=category.id,
        name=category.title,
        slug=category.slug,
        description=category.subtitle,
        image=category.image_key,
        image_url=category.image_url,
        subcategories=category.subcategories,
        status="active" if category.is_active else "disabled",
        created_at=category.created_at,
    )


def broadcast_catalog_market_change(market: Market) -> None:
    from app.core.cache import invalidate_catalog_markets

    invalidate_catalog_markets()
    realtime_manager.broadcast_event_sync(
        "catalog.market.changed",
        {
            "market_id": market.id,
            "slug": market.slug,
            "status": "active" if market.is_active else "disabled",
            "is_active": market.is_active,
        },
    )


def broadcast_catalog_category_change(category: Category) -> None:
    from app.core.cache import invalidate_catalog_categories, invalidate_catalog_products

    invalidate_catalog_categories()
    invalidate_catalog_products()
    realtime_manager.broadcast_event_sync(
        "catalog.category.changed",
        {
            "category_id": category.id,
            "slug": category.slug,
            "status": "active" if category.is_active else "disabled",
            "is_active": category.is_active,
        },
    )


def list_admin_stores(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminStoreRead]:
    require_admin(current_user)
    statement = select(Store).order_by(Store.created_at.desc())
    stores, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_store(store) for store in stores],
        has_more=has_more,
    )


def get_admin_store(db: Session, current_user: User, store_id: str) -> AdminStoreDetailRead:
    require_admin(current_user)
    store = db.scalar(select(Store).where(Store.id == store_id))
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found.")
    vendor = db.scalar(select(User).where(User.id == store.vendor_user_id)) if store.vendor_user_id else None
    market = db.scalar(select(Market).where(Market.id == store.market_id)) if store.market_id else None
    products = list(
        db.scalars(select(Product).where(Product.store_id == store.id).order_by(Product.created_at.desc())).all()
    )
    return _serialize_store_detail(db, store, vendor=vendor, market=market, products=products)


def update_admin_store_status(
    db: Session,
    current_user: User,
    store_id: str,
    payload: AdminStoreStatusUpdate,
) -> AdminStoreRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_STORE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported store status.")

    store = db.scalar(select(Store).where(Store.id == store_id))
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found.")

    store.status = payload.status
    store.is_active = payload.status == "active"
    db.commit()
    db.refresh(store)
    broadcast_catalog_store_change(store)
    return _serialize_store(store)


async def create_admin_store(
    db: Session,
    current_user: User,
    payload: AdminStoreUpsert,
    logo_image: UploadFile | None,
    banner_image: UploadFile | None,
) -> AdminStoreRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_STORE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported store status.")

    market = None
    if payload.market_id:
        market = db.scalar(select(Market).where(Market.id == payload.market_id))
        if not market:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found.")

    category_slug = _slugify(payload.category)
    taxonomy_entry = _taxonomy_lookup_by_slug().get(category_slug)
    store = Store(
        id=_generate_store_id(),
        slug=payload.slug or _slugify(payload.name) or _generate_store_id(),
        title=payload.name,
        category=taxonomy_entry["title"] if taxonomy_entry else payload.category,
        audience_slugs=_normalize_list(payload.audience_slugs),
        market_id=market.id if market else None,
        market_slug=market.slug if market else None,
        image_key=taxonomy_entry["image_key"] if taxonomy_entry else _infer_image_key(payload.category),
        image_url=await save_image_upload(logo_image, folder="stores/logo") if logo_image else None,
        image_banner_key=taxonomy_entry["image_key"] if taxonomy_entry else _infer_image_key(payload.category),
        image_banner_url=await save_image_upload(banner_image, folder="stores/banner") if banner_image else None,
        rating=4.6,
        address=payload.location,
        phone=None,
        email="support@odos.app",
        city=payload.city,
        region=payload.region,
        distance_km=None,
        travel_minutes=None,
        description=payload.description or payload.name,
        status=payload.status,
        vendor_user_id=None,
        sort_order=(db.scalar(select(func.coalesce(func.max(Store.sort_order), 0))) or 0) + 1,
        is_active=payload.status == "active",
    )
    db.add(store)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A store with that name already exists.",
        ) from exc
    db.refresh(store)
    broadcast_catalog_store_change(store)
    return _serialize_store(store)


def list_admin_markets(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminMarketRead]:
    require_admin(current_user)
    statement = select(Market).order_by(Market.sort_order.asc(), Market.title.asc())
    markets, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_market(market) for market in markets],
        has_more=has_more,
    )


async def create_admin_market(
    db: Session,
    current_user: User,
    payload: AdminMarketUpsert,
    image_file: UploadFile | None = None,
) -> AdminMarketRead:
    require_admin(current_user)
    market = Market(
        id=f"market-{uuid.uuid4().hex[:8]}",
        slug=payload.slug or _slugify(payload.name) or f"market-{uuid.uuid4().hex[:6]}",
        title=payload.name,
        image_key=payload.image or "market",
        sort_order=(db.scalar(select(func.coalesce(func.max(Market.sort_order), 0))) or 0) + 1,
        is_active=payload.status != "disabled",
    )
    if image_file:
        market.image_url = await save_image_upload(image_file, folder="markets")
    db.add(market)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A market with that name already exists.",
        ) from exc
    db.refresh(market)
    broadcast_catalog_market_change(market)
    return _serialize_market(market)


async def update_admin_market(
    db: Session,
    current_user: User,
    market_id: str,
    payload: AdminMarketUpsert,
    image_file: UploadFile | None = None,
) -> AdminMarketRead:
    require_admin(current_user)
    market = db.scalar(select(Market).where(Market.id == market_id))
    if not market:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found.")

    market.title = payload.name
    market.slug = payload.slug or _slugify(payload.name) or market.slug
    market.image_key = payload.image or market.image_key
    if image_file:
        if market.image_url:
            remove_media_file(market.image_url)
        market.image_url = await save_image_upload(image_file, folder="markets")
    market.is_active = payload.status != "disabled"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A market with that name already exists.",
        ) from exc
    db.refresh(market)
    broadcast_catalog_market_change(market)
    return _serialize_market(market)


def delete_admin_market(db: Session, current_user: User, market_id: str) -> None:
    require_admin(current_user)
    market = db.scalar(select(Market).where(Market.id == market_id))
    if not market:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found.")
    market.is_active = False
    db.commit()
    broadcast_catalog_market_change(market)


def list_admin_categories(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminCategoryRead]:
    require_admin(current_user)
    statement = select(Category).order_by(Category.sort_order.asc(), Category.title.asc())
    categories, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_category(category) for category in categories],
        has_more=has_more,
    )


async def create_admin_category(
    db: Session,
    current_user: User,
    payload: AdminCategoryUpsert,
    image_file: UploadFile | None,
) -> AdminCategoryRead:
    require_admin(current_user)
    category_slug = payload.slug or _slugify(payload.name) or f"category-{uuid.uuid4().hex[:6]}"
    taxonomy_entry = _taxonomy_lookup_by_slug().get(category_slug)
    category = Category(
        id=f"category-{uuid.uuid4().hex[:8]}",
        slug=category_slug,
        title=payload.name,
        subtitle=payload.description or payload.name,
        image_key=payload.image or (taxonomy_entry["image_key"] if taxonomy_entry else _infer_image_key(payload.name)),
        image_url=await save_image_upload(image_file, folder="categories") if image_file else None,
        subcategories=_normalize_list(payload.subcategories),
        sort_order=(db.scalar(select(func.coalesce(func.max(Category.sort_order), 0))) or 0) + 1,
        is_active=payload.status != "disabled",
    )
    db.add(category)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A category with that name already exists.",
        ) from exc
    db.refresh(category)
    broadcast_catalog_category_change(category)
    return _serialize_category(category)


async def update_admin_category(
    db: Session,
    current_user: User,
    category_id: str,
    payload: AdminCategoryUpsert,
    image_file: UploadFile | None,
) -> AdminCategoryRead:
    require_admin(current_user)
    category = db.scalar(select(Category).where(Category.id == category_id))
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")

    category.title = payload.name
    category.slug = payload.slug or _slugify(payload.name) or category.slug
    category.subtitle = payload.description or payload.name
    taxonomy_entry = _taxonomy_lookup_by_slug().get(category.slug)
    category.image_key = payload.image or category.image_key or (
        taxonomy_entry["image_key"] if taxonomy_entry else _infer_image_key(payload.name)
    )
    category.subcategories = _normalize_list(payload.subcategories)
    if image_file is not None:
        if category.image_url and category.image_url != category.image_key:
            remove_media_file(category.image_url)
        category.image_url = await save_image_upload(image_file, folder="categories")
    category.is_active = payload.status != "disabled"
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A category with that name already exists.",
        ) from exc
    db.refresh(category)
    broadcast_catalog_category_change(category)
    return _serialize_category(category)


def delete_admin_category(
    db: Session,
    current_user: User,
    category_id: str,
    *,
    permanent: bool = False,
) -> None:
    require_admin(current_user)
    category = db.scalar(select(Category).where(Category.id == category_id))
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")
    if permanent:
        if category.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Disable this category before deleting it permanently.",
            )
        if category.image_url:
            remove_media_file(category.image_url)
        deleted_snapshot = Category(
            id=category.id,
            slug=category.slug,
            title=category.title,
            subtitle=category.subtitle,
            image_key=category.image_key,
            image_url=category.image_url,
            subcategories=category.subcategories,
            sort_order=category.sort_order,
            is_active=False,
        )
        db.delete(category)
        db.commit()
        broadcast_catalog_category_change(deleted_snapshot)
        return
    category.is_active = False
    db.commit()
    broadcast_catalog_category_change(category)
