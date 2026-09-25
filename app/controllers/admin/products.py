"""Admin product management: creation, updates, status changes, and the
taxonomy resolution that decides where a product sits in the catalogue.
"""

import uuid

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.controllers.admin._shared import (
    _build_discount,
    _ensure_platform_store,
    _infer_image_key,
    _normalize_list,
    _slugify,
    _taxonomy_lookup_by_slug,
)
from app.controllers.vendor_controller import (
    broadcast_catalog_product_change,
    fetch_vendor_dashboard,
    serialize_vendor_product,
)
from app.core.admin_pagination import paginate_scalars
from app.core.auth import require_admin
from app.helpers.admin_audit import (
    log_admin_product_mutation,
)
from app.models import (
    Product,
    Store,
    User,
)
from app.schemas.admin import (
    AdminProductCreate,
    AdminProductRead,
    AdminProductStatusUpdate,
)
from app.schemas.pagination import AdminPageRead
from app.services.media_service import save_image_uploads
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


def _generate_product_id() -> str:
    return f"admin-product-{uuid.uuid4().hex[:12]}"




def _serialize_product(
    product: Product,
    *,
    store: Store | None = None,
    vendor: User | None = None,
) -> AdminProductRead:
    return AdminProductRead(
        id=product.id,
        store_id=product.store_id,
        store_name=store.title if store else None,
        store_slug=store.slug if store else None,
        store_category=store.category if store else None,
        store_location=store.address if store else None,
        store_region=store.region if store else None,
        store_city=store.city if store else None,
        vendor_id=str(product.vendor_user_id) if product.vendor_user_id else None,
        vendor_name=vendor.full_name if vendor else None,
        vendor_email=vendor.email if vendor else None,
        name=product.title,
        description=product.description or "",
        images=product.image_urls or ([product.image_url] if product.image_url else []),
        image_key=product.image_key,
        category=product.category or "",
        subcategory=product.subcategory,
        category_slugs=product.category_slugs,
        subcategory_slugs=product.subcategory_slugs,
        audience_slug=product.audience_slug,
        section=product.section,
        placement_tags=product.placement_tags,
        price=product.price,
        old_price=product.old_price,
        discount=product.discount,
        rating=product.rating,
        reviews=product.reviews,
        color_options=product.color_options,
        size_options=product.size_options,
        specifications=product.specifications,
        stock=product.stock,
        status=product.status,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def _resolve_product_taxonomy(
    *,
    category: str,
    subcategory: str | None,
    category_slugs: list[str] | None,
    subcategory_slugs: list[str] | None,
) -> tuple[str, str | None, list[str] | None, list[str] | None]:
    normalized_category_slugs = _normalize_list(
        category_slugs or [_slugify(category)]
    )
    normalized_subcategory_slugs = _normalize_list(
        subcategory_slugs or ([_slugify(subcategory)] if subcategory else None)
    )
    primary_category = category.strip()
    primary_subcategory = subcategory.strip() if subcategory else None

    taxonomy_lookup = _taxonomy_lookup_by_slug()
    if normalized_category_slugs:
        primary_entry = taxonomy_lookup.get(normalized_category_slugs[0])
        if primary_entry:
            primary_category = primary_entry["title"]

    if normalized_subcategory_slugs and normalized_category_slugs:
        for category_slug in normalized_category_slugs:
            entry = taxonomy_lookup.get(category_slug)
            if not entry:
                continue
            slug_to_title = {
                _slugify(item): item for item in entry.get("subcategories", [])
            }
            for sub_slug in normalized_subcategory_slugs:
                if sub_slug in slug_to_title:
                    primary_subcategory = slug_to_title[sub_slug]
                    return (
                        primary_category,
                        primary_subcategory,
                        normalized_category_slugs,
                        normalized_subcategory_slugs,
                    )

    return (
        primary_category,
        primary_subcategory,
        normalized_category_slugs,
        normalized_subcategory_slugs,
    )


def _serialize_admin_products(db: Session, products: list[Product]) -> list[AdminProductRead]:
    store_ids = {product.store_id for product in products if product.store_id}
    vendor_ids = {product.vendor_user_id for product in products if product.vendor_user_id}
    store_lookup = {
        store.id: store
        for store in db.scalars(select(Store).where(Store.id.in_(store_ids))).all()
    } if store_ids else {}
    vendor_lookup = {
        vendor.id: vendor
        for vendor in db.scalars(select(User).where(User.id.in_(vendor_ids))).all()
    } if vendor_ids else {}
    return [
        _serialize_product(
            product,
            store=store_lookup.get(product.store_id),
            vendor=vendor_lookup.get(product.vendor_user_id) if product.vendor_user_id else None,
        )
        for product in products
    ]


def list_admin_products(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminProductRead]:
    require_admin(current_user)
    statement = select(Product).order_by(
        case((Product.status == "pending", 0), else_=1),
        Product.updated_at.desc(),
        Product.created_at.desc(),
    )
    products, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=_serialize_admin_products(db, products),
        has_more=has_more,
    )


def get_admin_product(db: Session, current_user: User, product_id: str) -> AdminProductRead:
    require_admin(current_user)
    product = db.scalar(select(Product).where(Product.id == product_id))
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    store = None
    if product.store_id:
        store = db.scalar(select(Store).where(Store.id == product.store_id))
    vendor = None
    if product.vendor_user_id:
        vendor = db.scalar(select(User).where(User.id == product.vendor_user_id))
    return _serialize_product(
        product,
        store=store,
        vendor=vendor,
    )


def _get_store_for_admin_product(db: Session, store_id: str | None) -> Store:
    if store_id:
        store = db.scalar(select(Store).where(Store.id == store_id))
        if not store:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found.")
        return store

    return _ensure_platform_store(db)


async def create_admin_product(
    db: Session,
    current_user: User,
    payload: AdminProductCreate,
    images: list[UploadFile] | None,
) -> AdminProductRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_PRODUCT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported product status.")

    store = _get_store_for_admin_product(db, payload.store_id)

    image_urls = await save_image_uploads(images, folder="products")
    image_url = image_urls[0] if image_urls else None
    (
        primary_category,
        primary_subcategory,
        normalized_category_slugs,
        normalized_subcategory_slugs,
    ) = _resolve_product_taxonomy(
        category=payload.category,
        subcategory=payload.subcategory,
        category_slugs=payload.category_slugs,
        subcategory_slugs=payload.subcategory_slugs,
    )
    product = Product(
        id=_generate_product_id(),
        audience_slug=payload.audience_slug or ((store.audience_slugs or [None])[0] if store else None),
        section=payload.section,
        title=payload.name,
        category=primary_category,
        subcategory=primary_subcategory,
        category_slugs=normalized_category_slugs,
        subcategory_slugs=normalized_subcategory_slugs,
        price=payload.price,
        old_price=payload.old_price,
        discount=_build_discount(payload.price, payload.old_price),
        rating=payload.rating,
        reviews=payload.reviews,
        image_key=payload.image_key or _infer_image_key(primary_category),
        image_url=image_url,
        image_urls=image_urls or None,
        color_options=_normalize_list(payload.color_options),
        size_options=_normalize_list(payload.size_options),
        specifications=_normalize_list(payload.specifications),
        placement_tags=_normalize_list(payload.placement_tags),
        description=payload.description,
        stock=payload.stock,
        status=payload.status,
        store_id=store.id,
        vendor_user_id=store.vendor_user_id,
        sort_order=0,
        is_active=payload.status == "active",
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    log_admin_product_mutation(
        db,
        admin_user=current_user,
        action="product.created",
        product_id=product.id,
        after_state={
            "price": product.price,
            "stock": product.stock,
            "status": product.status,
        },
        metadata={"title": product.title, "store_id": product.store_id},
    )
    broadcast_catalog_product_change(product)
    vendor = None
    if product.vendor_user_id:
        vendor = db.scalar(select(User).where(User.id == product.vendor_user_id))
        if vendor:
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.product.updated",
                serialize_vendor_product(product).model_dump(mode="json"),
            )
            dashboard = fetch_vendor_dashboard(db, vendor)
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.dashboard.updated",
                dashboard.model_dump(mode="json"),
            )
    return _serialize_product(product, store=store, vendor=vendor)


async def update_admin_product(
    db: Session,
    current_user: User,
    product_id: str,
    payload: AdminProductCreate,
    images: list[UploadFile] | None,
) -> AdminProductRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_PRODUCT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported product status.")

    product = db.scalar(select(Product).where(Product.id == product_id))
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    before_state = {
        "price": product.price,
        "stock": product.stock,
        "status": product.status,
    }
    store = _get_store_for_admin_product(db, payload.store_id)
    uploaded_image_urls = await save_image_uploads(images, folder="products")
    existing_image_urls = list(product.image_urls or ([] if not product.image_url else [product.image_url]))
    next_image_urls = existing_image_urls + uploaded_image_urls if uploaded_image_urls else existing_image_urls

    (
        primary_category,
        primary_subcategory,
        normalized_category_slugs,
        normalized_subcategory_slugs,
    ) = _resolve_product_taxonomy(
        category=payload.category,
        subcategory=payload.subcategory,
        category_slugs=payload.category_slugs,
        subcategory_slugs=payload.subcategory_slugs,
    )

    product.audience_slug = payload.audience_slug or ((store.audience_slugs or [None])[0] if store else None)
    product.section = payload.section
    product.title = payload.name
    product.category = primary_category
    product.subcategory = primary_subcategory
    product.category_slugs = normalized_category_slugs
    product.subcategory_slugs = normalized_subcategory_slugs
    product.price = payload.price
    product.old_price = payload.old_price
    product.discount = _build_discount(payload.price, payload.old_price)
    product.rating = payload.rating
    product.reviews = payload.reviews
    product.image_key = payload.image_key or product.image_key or _infer_image_key(primary_category)
    product.image_urls = next_image_urls or None
    product.image_url = next_image_urls[0] if next_image_urls else None
    product.color_options = _normalize_list(payload.color_options)
    product.size_options = _normalize_list(payload.size_options)
    product.specifications = _normalize_list(payload.specifications)
    product.placement_tags = _normalize_list(payload.placement_tags)
    product.description = payload.description
    if int(product.stock or 0) != int(payload.stock):
        from app.services.inventory_service import record_stock_change

        record_stock_change(
            db,
            product,
            new_stock=int(payload.stock),
            reason="system",
            note="Updated by admin",
            actor=current_user,
        )
    else:
        product.stock = payload.stock
    product.status = payload.status
    product.store_id = store.id
    product.vendor_user_id = store.vendor_user_id
    product.is_active = payload.status == "active"

    db.commit()
    db.refresh(product)
    log_admin_product_mutation(
        db,
        admin_user=current_user,
        action="product.updated",
        product_id=product.id,
        before_state=before_state,
        after_state={
            "price": product.price,
            "stock": product.stock,
            "status": product.status,
        },
        metadata={"title": product.title, "store_id": product.store_id},
    )
    broadcast_catalog_product_change(product)
    vendor = None
    if product.vendor_user_id:
        vendor = db.scalar(select(User).where(User.id == product.vendor_user_id))
        if vendor:
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.product.updated",
                serialize_vendor_product(product).model_dump(mode="json"),
            )
            dashboard = fetch_vendor_dashboard(db, vendor)
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.dashboard.updated",
                dashboard.model_dump(mode="json"),
            )
    return _serialize_product(
        product,
        store=store,
        vendor=vendor,
    )


def update_admin_product_status(
    db: Session,
    current_user: User,
    product_id: str,
    payload: AdminProductStatusUpdate,
) -> AdminProductRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_PRODUCT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported product status.")

    product = db.scalar(select(Product).where(Product.id == product_id))
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")

    product.status = payload.status
    product.is_active = payload.status == "active"
    db.commit()
    db.refresh(product)
    broadcast_catalog_product_change(product)
    store = None
    if product.store_id:
        store = db.scalar(select(Store).where(Store.id == product.store_id))
    vendor = None
    if product.vendor_user_id:
        vendor = db.scalar(select(User).where(User.id == product.vendor_user_id))
        if vendor:
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.product.updated",
                serialize_vendor_product(product).model_dump(mode="json"),
            )
            dashboard = fetch_vendor_dashboard(db, vendor)
            realtime_manager.publish_user_event_sync(
                str(vendor.id),
                "vendor.dashboard.updated",
                dashboard.model_dump(mode="json"),
            )
    return _serialize_product(
        product,
        store=store,
        vendor=vendor,
    )
