from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.controllers.auth_controller import build_auth_token, login_user
from app.core.security import hash_password
from app.core.catalog_taxonomy import ODOS_CATEGORY_TAXONOMY
from app.controllers.vendor_controller import (
    approve_vendor_application,
    list_vendor_applications,
    reject_vendor_application,
)
from app.services.media_service import remove_media_file, save_image_upload, save_image_uploads
from app.models import (
    Category,
    Market,
    NotificationEvent,
    NotificationRead,
    Order,
    Product,
    Store,
    User,
    UserRole,
    VendorApplication,
    VendorStatus,
)
from app.schemas.admin import (
    AdminBootstrapStatusRead,
    AdminCategoryRead,
    AdminCategoryUpsert,
    AdminDashboardRead,
    AdminDashboardStatsRead,
    AdminMarketRead,
    AdminMarketUpsert,
    AdminNotificationRead,
    AdminOrderRead,
    AdminOrderStatusUpdate,
    AdminProductCreate,
    AdminProductRead,
    AdminProductStatusUpdate,
    AdminStoreRead,
    AdminStoreUpsert,
    AdminStoreStatusUpdate,
    AdminUserRead,
    AdminUserStatusUpdate,
    AdminVendorRead,
    AdminVendorStatusUpdate,
    NotificationMarkReadResponse,
)
from app.schemas.user import AuthToken, UserCreate, UserLogin

SUPPORTED_ACCOUNT_STATUSES = {"active", "blocked", "inactive"}
SUPPORTED_VENDOR_STATUSES = {"active", "suspended"}
SUPPORTED_STORE_STATUSES = {"active", "suspended", "draft"}
SUPPORTED_PRODUCT_STATUSES = {"active", "hidden", "suspended"}
SUPPORTED_ORDER_STATUSES = {
    "pending",
    "confirmed",
    "processing",
    "ready",
    "out_for_delivery",
    "delivered",
    "cancelled",
}


def _slugify(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower().strip())
    return "-".join(segment for segment in cleaned.split("-") if segment)[:80]


def _normalize_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    cleaned = [value.strip() for value in values if value and value.strip()]
    return cleaned or None


def _generate_store_id() -> str:
    return f"store-{uuid.uuid4().hex[:10]}"


def _generate_product_id() -> str:
    return f"admin-product-{uuid.uuid4().hex[:12]}"


def require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required for this action.",
        )


def _account_status(user: User) -> str:
    return "active" if user.is_active else "blocked"


def _notification_type(kind: str) -> str:
    normalized = kind.lower()
    if "vendor" in normalized:
        return "vendor"
    if "order" in normalized:
        return "order"
    if "store" in normalized:
        return "store"
    if "user" in normalized or "account" in normalized:
        return "user"
    return "system"


def _payment_status(order: Order) -> str:
    if order.status == "cancelled":
        return "refunded"
    return "paid"


def _serialize_user(user: User) -> AdminUserRead:
    return AdminUserRead(
        id=user.id,
        full_name=user.full_name,
        email=user.email,
        phone_number=user.phone_number,
        avatar_url=user.avatar_url,
        roles=user.roles,
        vendor_status=user.vendor_status,
        account_status=_account_status(user),
        joined_at=user.created_at,
    )


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


def _serialize_product(product: Product, *, store_name: str | None = None) -> AdminProductRead:
    return AdminProductRead(
        id=product.id,
        store_id=product.store_id,
        store_name=store_name,
        vendor_id=str(product.vendor_user_id) if product.vendor_user_id else None,
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
    )


def _store_name_lookup(db: Session, order: Order) -> str:
    product_ids = [item.product_id for item in order.items]
    if not product_ids:
        return "Marketplace"

    store_ids = list(
        db.scalars(select(Product.store_id).where(Product.id.in_(product_ids))).all()
    )
    first_store_id = next((store_id for store_id in store_ids if store_id), None)
    if not first_store_id:
        return "Marketplace"

    store = db.scalar(select(Store).where(Store.id == first_store_id))
    return store.title if store else "Marketplace"


def _serialize_order(db: Session, order: Order) -> AdminOrderRead:
    return AdminOrderRead(
        id=order.id,
        order_number=order.order_number,
        customer_name=order.address_full_name,
        store_name=_store_name_lookup(db, order),
        total_amount=round(order.total_amount, 2),
        status=order.vendor_status or order.status,
        payment_status=_payment_status(order),
        created_at=order.created_at,
    )


def _serialize_notification(notification: NotificationEvent, *, is_read: bool) -> AdminNotificationRead:
    return AdminNotificationRead(
        id=notification.id,
        type=_notification_type(notification.kind),
        title=notification.title,
        message=notification.body,
        read=is_read,
        created_at=notification.created_at,
    )


def _vendor_application_by_user(db: Session, user_id: uuid.UUID) -> VendorApplication | None:
    return db.scalar(select(VendorApplication).where(VendorApplication.user_id == user_id))


def _vendor_activity_summary(db: Session, vendor_user_id: uuid.UUID) -> tuple[int, int, int, float]:
    stores_count = db.scalar(
        select(func.count(Store.id)).where(Store.vendor_user_id == vendor_user_id)
    ) or 0
    products = list(
        db.scalars(select(Product).where(Product.vendor_user_id == vendor_user_id)).all()
    )
    product_ids = {product.id for product in products}
    if not product_ids:
        return stores_count, len(products), 0, 0.0

    orders = list(
        db.scalars(
            select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc())
        ).all()
    )

    total_orders = 0
    total_sales = 0.0
    for order in orders:
        matching_items = [item for item in order.items if item.product_id in product_ids]
        if not matching_items:
            continue
        total_orders += 1
        if order.vendor_status in {"confirmed", "processing", "ready", "delivered"}:
            total_sales += sum(item.line_total for item in matching_items)

    return stores_count, len(products), total_orders, round(total_sales, 2)


def _serialize_vendor(db: Session, user: User) -> AdminVendorRead:
    application = _vendor_application_by_user(db, user.id)
    stores_count, products_count, total_orders, total_sales = _vendor_activity_summary(db, user.id)
    return AdminVendorRead(
        id=str(user.id),
        user_id=user.id,
        business_name=application.business_name if application else user.full_name,
        business_category=application.business_category if application else "General",
        status="suspended" if user.vendor_status == VendorStatus.SUSPENDED else "active",
        email=user.email,
        phone_number=user.phone_number,
        total_stores=stores_count,
        total_products=products_count,
        total_orders=total_orders,
        total_sales=total_sales,
        joined_at=user.created_at,
    )


def _build_discount(price: int, old_price: int | None) -> str | None:
    if old_price is None or old_price <= 0 or old_price <= price:
        return None

    percentage = round(((old_price - price) / old_price) * 100)
    return f"{percentage}% off"


def _taxonomy_lookup_by_slug() -> dict[str, dict]:
    return {entry["slug"]: entry for entry in ODOS_CATEGORY_TAXONOMY}


def _normalize_slug(value: str) -> str:
    return _slugify(value)


def _normalize_string_list(values: list[str] | None) -> list[str] | None:
    return _normalize_list(values)


def _resolve_product_taxonomy(
    *,
    category: str,
    subcategory: str | None,
    category_slugs: list[str] | None,
    subcategory_slugs: list[str] | None,
) -> tuple[str, str | None, list[str] | None, list[str] | None]:
    normalized_category_slugs = _normalize_string_list(
        category_slugs or [_normalize_slug(category)]
    )
    normalized_subcategory_slugs = _normalize_string_list(
        subcategory_slugs or ([_normalize_slug(subcategory)] if subcategory else None)
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
                _normalize_slug(item): item for item in entry.get("subcategories", [])
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


def _infer_image_key(category: str) -> str:
    normalized = category.strip().lower()
    if "bag" in normalized:
        return "bag"
    if "shoe" in normalized or "sandal" in normalized or "slipper" in normalized:
        return "shoe5"
    if "dress" in normalized or "fashion" in normalized or "clothing" in normalized:
        return "dress"
    if "men" in normalized or "gents" in normalized:
        return "gents"
    if "beauty" in normalized or "cosmetic" in normalized:
        return "cosmetics"
    if "sport" in normalized:
        return "sports"
    return "bag"


def _ensure_platform_store(db: Session) -> Store:
    admin_avatar_url = db.scalar(
        select(User.avatar_url)
        .where(
            User.role == UserRole.ADMIN,
            User.avatar_url.is_not(None),
        )
        .order_by(User.updated_at.desc())
        .limit(1)
    )
    existing = db.scalar(select(Store).where(Store.slug == "odos-official"))
    if existing:
        if admin_avatar_url:
            _sync_platform_store_avatar(existing, admin_avatar_url)
        return existing

    store = Store(
        id=_generate_store_id(),
        slug="odos-official",
        title="ODOS Official",
        category="Marketplace",
        market_id=None,
        market_slug=None,
        image_key="bag",
        image_url=admin_avatar_url,
        rating=4.8,
        address="ODOS Marketplace",
        phone=None,
        email="support@odos.app",
        city="Accra",
        region="Greater Accra",
        distance_km=None,
        travel_minutes=None,
        description="Platform-managed catalog products curated by ODOS.",
        image_banner_key=None,
        image_banner_url=admin_avatar_url,
        status="active",
        vendor_user_id=None,
        sort_order=0,
        is_active=True,
    )
    db.add(store)
    db.flush()
    return store


def _sync_platform_store_avatar(store: Store, avatar_url: str | None) -> None:
    if avatar_url:
        store.image_url = avatar_url
        store.image_banner_url = avatar_url


def login_admin_user(db: Session, credentials: UserLogin) -> AuthToken:
    session = login_user(db, credentials)
    if session.user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account does not have admin access.",
        )
    return session


def get_admin_bootstrap_status(db: Session) -> AdminBootstrapStatusRead:
    admin_count = db.scalar(
        select(func.count(User.id)).where(User.role == UserRole.ADMIN)
    ) or 0
    return AdminBootstrapStatusRead(bootstrap_enabled=admin_count == 0)


def bootstrap_first_admin(db: Session, payload: UserCreate) -> AuthToken:
    if not get_admin_bootstrap_status(db).bootstrap_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin bootstrap is no longer available.",
        )

    normalized_email = payload.email.lower()
    existing_user = db.scalar(select(User).where(User.email == normalized_email))
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    if payload.phone_number:
        existing_phone = db.scalar(
            select(User).where(User.phone_number == payload.phone_number)
        )
        if existing_phone:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this phone number already exists.",
            )

    user = User(
        full_name=payload.full_name,
        email=normalized_email,
        phone_number=payload.phone_number,
        hashed_password=hash_password(payload.password),
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with these details already exists.",
        ) from None

    return build_auth_token(db, user)


def get_admin_me(current_user: User) -> User:
    require_admin(current_user)
    return current_user


async def update_admin_profile(
    db: Session,
    current_user: User,
    *,
    full_name: str | None,
    phone_number: str | None,
    avatar_image: UploadFile | None,
) -> User:
    require_admin(current_user)

    if full_name is not None:
        cleaned_name = full_name.strip()
        if len(cleaned_name) < 2:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Full name must be at least 2 characters long.",
            )
        current_user.full_name = cleaned_name

    if phone_number is not None:
        cleaned_phone = phone_number.strip() or None
        if cleaned_phone:
            existing_phone = db.scalar(
                select(User).where(
                    User.phone_number == cleaned_phone,
                    User.id != current_user.id,
                )
            )
            if existing_phone:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A user with this phone number already exists.",
                )
        current_user.phone_number = cleaned_phone

    if avatar_image is not None:
        previous_avatar_url = current_user.avatar_url
        current_user.avatar_url = await save_image_upload(avatar_image, folder="users/avatars")
        if previous_avatar_url and previous_avatar_url != current_user.avatar_url:
            remove_media_file(previous_avatar_url)

    if current_user.avatar_url:
        platform_store = _ensure_platform_store(db)
        _sync_platform_store_avatar(platform_store, current_user.avatar_url)

    try:
        db.commit()
        db.refresh(current_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="We couldn't save those admin profile changes.",
        ) from None

    return current_user


def get_admin_dashboard(db: Session, current_user: User) -> AdminDashboardRead:
    require_admin(current_user)

    stats = AdminDashboardStatsRead(
        total_users=db.scalar(select(func.count(User.id))) or 0,
        total_vendors=db.scalar(
            select(func.count(User.id)).where(
                User.vendor_status.in_([VendorStatus.APPROVED, VendorStatus.SUSPENDED])
            )
        )
        or 0,
        pending_vendor_applications=db.scalar(
            select(func.count(VendorApplication.id)).where(
                VendorApplication.status.in_([VendorStatus.PENDING, VendorStatus.UNDER_REVIEW])
            )
        )
        or 0,
        total_stores=db.scalar(select(func.count(Store.id))) or 0,
        total_products=db.scalar(select(func.count(Product.id))) or 0,
        total_orders=db.scalar(select(func.count(Order.id))) or 0,
        pending_orders=db.scalar(
            select(func.count(Order.id)).where(
                Order.vendor_status.in_(["pending", "confirmed", "processing", "ready"])
            )
        )
        or 0,
        total_revenue=round(
            float(db.scalar(select(func.coalesce(func.sum(Order.total_amount), 0.0))) or 0.0),
            2,
        ),
    )

    recent_orders = list(
        db.scalars(
            select(Order)
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc())
            .limit(5)
        ).all()
    )
    recent_notifications = list(
        db.scalars(
            select(NotificationEvent)
            .order_by(NotificationEvent.created_at.desc())
            .limit(5)
        ).all()
    )
    read_keys = set(
        db.scalars(
            select(NotificationRead.notification_key).where(NotificationRead.user_id == current_user.id)
        ).all()
    )

    return AdminDashboardRead(
        stats=stats,
        recent_orders=[_serialize_order(db, order) for order in recent_orders],
        recent_vendor_applications=[
            item.model_dump() for item in list_vendor_applications(db, current_user)[:5]
        ],
        recent_notifications=[
            _serialize_notification(notification, is_read=str(notification.id) in read_keys)
            for notification in recent_notifications
        ],
    )


def list_admin_users(db: Session, current_user: User) -> list[AdminUserRead]:
    require_admin(current_user)
    users = list(db.scalars(select(User).order_by(User.created_at.desc())).all())
    return [_serialize_user(user) for user in users]


def get_admin_user(db: Session, current_user: User, user_id: str) -> AdminUserRead:
    require_admin(current_user)
    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return _serialize_user(user)


def update_admin_user_status(
    db: Session,
    current_user: User,
    user_id: str,
    payload: AdminUserStatusUpdate,
) -> AdminUserRead:
    require_admin(current_user)
    if payload.account_status not in SUPPORTED_ACCOUNT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported account status.")

    user = db.scalar(select(User).where(User.id == user_id))
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    user.is_active = payload.account_status == "active"
    db.commit()
    db.refresh(user)
    return _serialize_user(user)


def list_admin_vendors(db: Session, current_user: User) -> list[AdminVendorRead]:
    require_admin(current_user)
    vendors = list(
        db.scalars(
            select(User)
            .where(User.vendor_status.in_([VendorStatus.APPROVED, VendorStatus.SUSPENDED]))
            .order_by(User.created_at.desc())
        ).all()
    )
    return [_serialize_vendor(db, vendor) for vendor in vendors]


def get_admin_vendor(db: Session, current_user: User, vendor_id: str) -> AdminVendorRead:
    require_admin(current_user)
    vendor = db.scalar(select(User).where(User.id == vendor_id))
    if not vendor or vendor.vendor_status not in {VendorStatus.APPROVED, VendorStatus.SUSPENDED}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found.")
    return _serialize_vendor(db, vendor)


def update_admin_vendor_status(
    db: Session,
    current_user: User,
    vendor_id: str,
    payload: AdminVendorStatusUpdate,
) -> AdminVendorRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_VENDOR_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported vendor status.")

    vendor = db.scalar(select(User).where(User.id == vendor_id))
    if not vendor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found.")

    if payload.status == "suspended":
        vendor.vendor_status = VendorStatus.SUSPENDED
        for store in db.scalars(select(Store).where(Store.vendor_user_id == vendor.id)).all():
            store.status = "suspended"
            store.is_active = False
    else:
        vendor.vendor_status = VendorStatus.APPROVED
        if vendor.role != UserRole.ADMIN:
            vendor.role = UserRole.VENDOR
        for store in db.scalars(select(Store).where(Store.vendor_user_id == vendor.id)).all():
            if store.status == "suspended":
                store.status = "active"
                store.is_active = True

    db.commit()
    db.refresh(vendor)
    return _serialize_vendor(db, vendor)


def list_admin_stores(db: Session, current_user: User) -> list[AdminStoreRead]:
    require_admin(current_user)
    stores = list(db.scalars(select(Store).order_by(Store.created_at.desc())).all())
    return [_serialize_store(store) for store in stores]


def get_admin_store(db: Session, current_user: User, store_id: str) -> AdminStoreRead:
    require_admin(current_user)
    store = db.scalar(select(Store).where(Store.id == store_id))
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found.")
    return _serialize_store(store)


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

    category_slug = _normalize_slug(payload.category)
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
    db.commit()
    db.refresh(store)
    return _serialize_store(store)


def list_admin_markets(db: Session, current_user: User) -> list[AdminMarketRead]:
    require_admin(current_user)
    markets = list(
        db.scalars(select(Market).order_by(Market.sort_order.asc(), Market.title.asc())).all()
    )
    return [_serialize_market(market) for market in markets]


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
        market.image_url = await save_image_upload(image_file, "markets")
    db.add(market)
    db.commit()
    db.refresh(market)
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
        market.image_url = await save_image_upload(image_file, "markets")
    market.is_active = payload.status != "disabled"
    db.commit()
    db.refresh(market)
    return _serialize_market(market)


def delete_admin_market(db: Session, current_user: User, market_id: str) -> None:
    require_admin(current_user)
    market = db.scalar(select(Market).where(Market.id == market_id))
    if not market:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Market not found.")
    market.is_active = False
    db.commit()


def list_admin_categories(db: Session, current_user: User) -> list[AdminCategoryRead]:
    require_admin(current_user)
    categories = list(
        db.scalars(select(Category).order_by(Category.sort_order.asc(), Category.title.asc())).all()
    )
    return [_serialize_category(category) for category in categories]


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
    db.commit()
    db.refresh(category)
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
    db.commit()
    db.refresh(category)
    return _serialize_category(category)


def delete_admin_category(db: Session, current_user: User, category_id: str) -> None:
    require_admin(current_user)
    category = db.scalar(select(Category).where(Category.id == category_id))
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")
    category.is_active = False
    db.commit()


def list_admin_products(db: Session, current_user: User) -> list[AdminProductRead]:
    require_admin(current_user)
    products = list(db.scalars(select(Product).order_by(Product.created_at.desc())).all())
    store_ids = {product.store_id for product in products if product.store_id}
    store_lookup = {
        store.id: store.title
        for store in db.scalars(select(Store).where(Store.id.in_(store_ids))).all()
    } if store_ids else {}
    return [
        _serialize_product(product, store_name=store_lookup.get(product.store_id))
        for product in products
    ]


def get_admin_product(db: Session, current_user: User, product_id: str) -> AdminProductRead:
    require_admin(current_user)
    product = db.scalar(select(Product).where(Product.id == product_id))
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found.")
    store_name = None
    if product.store_id:
        store = db.scalar(select(Store).where(Store.id == product.store_id))
        store_name = store.title if store else None
    return _serialize_product(product, store_name=store_name)


async def create_admin_product(
    db: Session,
    current_user: User,
    payload: AdminProductCreate,
    images: list[UploadFile] | None,
) -> AdminProductRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_PRODUCT_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported product status.")

    store = None
    if payload.store_id:
        store = db.scalar(select(Store).where(Store.id == payload.store_id))
        if not store:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found.")
    else:
        store = _ensure_platform_store(db)

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
        store_id=store.id if store else None,
        vendor_user_id=None,
        sort_order=0,
        is_active=payload.status == "active",
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return _serialize_product(product, store_name=store.title if store else None)


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
    store_name = None
    if product.store_id:
        store = db.scalar(select(Store).where(Store.id == product.store_id))
        store_name = store.title if store else None
    return _serialize_product(product, store_name=store_name)


def list_admin_orders(db: Session, current_user: User) -> list[AdminOrderRead]:
    require_admin(current_user)
    orders = list(
        db.scalars(
            select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc())
        ).all()
    )
    return [_serialize_order(db, order) for order in orders]


def get_admin_order(db: Session, current_user: User, order_id: str) -> AdminOrderRead:
    require_admin(current_user)
    order = db.scalar(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")
    return _serialize_order(db, order)


def update_admin_order_status(
    db: Session,
    current_user: User,
    order_id: str,
    payload: AdminOrderStatusUpdate,
) -> AdminOrderRead:
    require_admin(current_user)
    if payload.status not in SUPPORTED_ORDER_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported order status.")

    order = db.scalar(
        select(Order).options(selectinload(Order.items)).where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found.")

    order.vendor_status = payload.status
    if payload.status == "delivered":
        order.status = "delivered"
        order.delivered_at = datetime.now(UTC)
        order.cancelled_at = None
        order.cancellation_reason = None
        order.progress = 1
        order.tracking_eta = None
    elif payload.status == "cancelled":
        order.status = "cancelled"
        order.cancelled_at = datetime.now(UTC)
        order.cancellation_reason = "Cancelled by admin"
        order.progress = 0
        order.tracking_eta = None
    elif payload.status == "out_for_delivery":
        order.status = "processing"
        order.progress = 0.9
        order.tracking_eta = "Out for delivery"
    else:
        order.status = "processing"
        progress_map = {
            "pending": 0.1,
            "confirmed": 0.2,
            "processing": 0.45,
            "ready": 0.75,
        }
        order.progress = progress_map.get(payload.status, order.progress)
        order.tracking_eta = payload.status.replace("_", " ").title()

    db.commit()
    db.refresh(order)
    return _serialize_order(db, order)


def list_admin_notifications(db: Session, current_user: User) -> list[AdminNotificationRead]:
    require_admin(current_user)
    notifications = list(
        db.scalars(select(NotificationEvent).order_by(NotificationEvent.created_at.desc())).all()
    )
    read_keys = set(
        db.scalars(
            select(NotificationRead.notification_key).where(NotificationRead.user_id == current_user.id)
        ).all()
    )
    return [
        _serialize_notification(notification, is_read=str(notification.id) in read_keys)
        for notification in notifications
    ]


def mark_admin_notification_read(
    db: Session,
    current_user: User,
    notification_id: str,
) -> NotificationMarkReadResponse:
    require_admin(current_user)
    notification = db.scalar(select(NotificationEvent).where(NotificationEvent.id == notification_id))
    if not notification:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found.")

    existing = db.scalar(
        select(NotificationRead).where(
            NotificationRead.user_id == current_user.id,
            NotificationRead.notification_key == str(notification.id),
        )
    )
    if not existing:
        db.add(
            NotificationRead(
                user_id=current_user.id,
                notification_key=str(notification.id),
            )
        )
        db.commit()

    return NotificationMarkReadResponse(notification_key=str(notification.id))
