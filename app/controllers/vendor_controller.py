import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event
from app.core.product_taxonomy import resolve_product_taxonomy
from app.controllers.voucher_controller import (
    build_voucher_reward_text,
    assign_voucher_to_user,
    validate_voucher_configuration,
    voucher_status,
)
from app.controllers.wallet_controller import (
    publish_vendor_wallet_updates,
    settle_vendor_wallets_for_order,
)
from app.models import (
    Market,
    Order,
    Product,
    Store,
    User,
    UserRole,
    VendorApplication,
    VendorStatus,
    VendorWallet,
    Voucher,
    VoucherRedemption,
)
from app.schemas.vendor import (
    VendorApplicationListItem,
    VendorApplicationRead,
    VendorDashboardRead,
    VendorOrderItemRead,
    VendorOrderRead,
    VendorOrderStatusUpdate,
    VendorProductCreate,
    VendorProductRead,
    VendorProductUpdate,
    VendorProfileRead,
    VendorStoreRead,
    VendorVoucherGiftPayload,
    VendorVoucherRead,
    VendorVoucherUpsert,
)
from app.schemas.order import OrderRead
from app.services.email_service import (
    send_vendor_application_approved_email,
    send_vendor_application_pending_email,
)
from app.services.media_service import remove_media_file, save_image_upload, save_image_uploads
from app.services.realtime_service import realtime_manager

logger = logging.getLogger(__name__)
VENDOR_ACTIVE_ORDER_STATUSES = {"pending", "confirmed", "processing", "ready"}
VENDOR_ALLOWED_STATUSES = {
    "pending",
    "confirmed",
    "processing",
    "ready",
    "delivered",
    "cancelled",
}


def slugify(value: str) -> str:
    return (
        value.strip()
        .lower()
        .replace("&", " and ")
        .replace("/", " ")
        .replace("'", "")
    )


def normalize_slug(value: str) -> str:
    cleaned = "".join(char if char.isalnum() else "-" for char in slugify(value))
    collapsed = "-".join(segment for segment in cleaned.split("-") if segment)
    return collapsed[:80] or f"store-{uuid.uuid4().hex[:8]}"


def generate_store_id() -> str:
    return f"store-{uuid.uuid4().hex[:10]}"


def generate_product_id() -> str:
    return f"vendor-product-{uuid.uuid4().hex[:12]}"


def normalize_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    cleaned = [value.strip() for value in values if value and value.strip()]
    return cleaned or None


def build_discount(price: int, old_price: int | None) -> str | None:
    if old_price is None or old_price <= 0 or old_price <= price:
        return None
    percentage = round(((old_price - price) / old_price) * 100)
    return f"{percentage}% off"


def build_unique_store_slug(db: Session, title: str, store_id: str | None = None) -> str:
    base_slug = normalize_slug(title)
    candidate = base_slug
    suffix = 1

    while True:
        existing = db.scalar(select(Store).where(Store.slug == candidate))
        if not existing or existing.id == store_id:
            return candidate
        suffix += 1
        candidate = f"{base_slug}-{suffix}"


def _dispatch_vendor_application_pending_email(
    *,
    user: User,
    application: VendorApplication,
) -> None:
    try:
        send_vendor_application_pending_email(
            to_email=user.email,
            to_name=user.full_name,
            store_name=application.store_name,
            business_category=application.business_category,
            submitted_at_label=(
                application.submitted_at or application.created_at or datetime.now(UTC)
            ).strftime("%d %b %Y, %I:%M %p UTC"),
        )
    except Exception:
        logger.exception(
            "Failed to send vendor application pending email to %s",
            user.email,
        )


def _dispatch_vendor_application_approved_email(
    *,
    user: User,
    application: VendorApplication,
) -> None:
    try:
        send_vendor_application_approved_email(
            to_email=user.email,
            to_name=user.full_name,
            store_name=application.store_name,
        )
    except Exception:
        logger.exception(
            "Failed to send vendor application approved email to %s",
            user.email,
        )


def require_admin(user: User) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access is required for this action.",
        )


def require_vendor_access(user: User) -> None:
    if user.role == UserRole.ADMIN:
        return

    if user.vendor_status == VendorStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vendor access is currently suspended for this account.",
        )

    if user.vendor_status != VendorStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your vendor access is not approved yet.",
        )


def get_market(db: Session, market_id: str | None) -> Market | None:
    if not market_id:
        return None

    market = db.scalar(
        select(Market).where(
            Market.id == market_id,
            Market.is_active.is_(True),
        )
    )
    if not market:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected market was not found.",
        )

    return market


def get_vendor_application(db: Session, user: User) -> VendorApplication | None:
    return db.scalar(
        select(VendorApplication).where(VendorApplication.user_id == user.id)
    )


def get_vendor_store(db: Session, user: User) -> Store | None:
    return db.scalar(
        select(Store).where(Store.vendor_user_id == user.id)
    )


def serialize_vendor_profile(
    *,
    user: User,
    application: VendorApplication,
    store: Store | None,
) -> VendorProfileRead:
    return VendorProfileRead(
        id=str(user.id),
        user_id=user.id,
        status=user.vendor_status,
        business_name=application.business_name,
        business_category=application.business_category,
        business_description=application.business_description,
        phone_number=application.phone_number,
        whatsapp_number=application.whatsapp_number,
        created_at=application.created_at,
        store_id=store.id if store else None,
        store_name=store.title if store else application.store_name,
        rejection_reason=user.vendor_rejection_reason or application.rejection_reason,
    )


def serialize_vendor_store(store: Store) -> VendorStoreRead:
    return VendorStoreRead(
        id=store.id,
        vendor_id=str(store.vendor_user_id),
        name=store.title,
        slug=store.slug,
        description=store.description or "",
        category=store.category or "",
        audience_slugs=store.audience_slugs,
        market_id=store.market_id,
        market_slug=store.market_slug,
        location=store.address,
        phone=store.phone,
        latitude=store.latitude,
        longitude=store.longitude,
        instagram_url=store.instagram_url,
        facebook_url=store.facebook_url,
        tiktok_url=store.tiktok_url,
        twitter_url=store.twitter_url,
        whatsapp_url=store.whatsapp_url,
        website_url=store.website_url,
        region=store.region or "",
        city=store.city or "",
        banner_image_url=store.image_banner_url,
        logo_image_url=store.image_url,
        status=store.status,
    )


def serialize_vendor_product(product: Product) -> VendorProductRead:
    return VendorProductRead(
        id=product.id,
        store_id=product.store_id or "",
        vendor_id=str(product.vendor_user_id),
        name=product.title,
        description=product.description or "",
        category=product.category or "",
        category_slug=(product.category_slugs or [None])[0],
        subcategory=product.subcategory,
        price=product.price,
        old_price=product.old_price,
        discount=product.discount,
        stock=product.stock,
        image_key=product.image_key,
        image_url=product.image_url,
        image_urls=product.image_urls,
        placement_tags=product.placement_tags,
        color_options=product.color_options,
        size_options=product.size_options,
        specifications=product.specifications,
        is_returnable=product.is_returnable,
        status=product.status,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def broadcast_catalog_product_change(
    product: Product,
    *,
    status: str | None = None,
    is_active: bool | None = None,
) -> None:
    from app.core.cache import invalidate_catalog_product

    invalidate_catalog_product(product.id)
    realtime_manager.broadcast_event_sync(
        "catalog.product.changed",
        {
            "product_id": product.id,
            "store_id": product.store_id,
            "status": status if status is not None else product.status,
            "is_active": is_active if is_active is not None else product.is_active,
            "category": product.category,
            "subcategory": product.subcategory,
            "audience_slug": product.audience_slug,
            "section": product.section,
        },
    )


def broadcast_catalog_store_change(store: Store) -> None:
    from app.core.cache import invalidate_catalog_store

    invalidate_catalog_store(store.id)
    realtime_manager.broadcast_event_sync(
        "catalog.store.changed",
        {
            "store_id": store.id,
            "status": store.status,
            "market_slug": store.market_slug,
            "category": store.category,
            "audience_slugs": store.audience_slugs,
        },
    )


def list_vendor_orders_payloads(db: Session, user: User) -> list[VendorOrderRead]:
    orders = list(
        db.scalars(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.user))
            .order_by(Order.placed_at.desc(), Order.created_at.desc())
        ).all()
    )

    payloads: list[VendorOrderRead] = []
    for order in orders:
        matching_items = [
            item
            for item in order.items
            if item.vendor_user_id == user.id
        ]
        if not matching_items:
            continue

        payloads.append(
            VendorOrderRead(
                id=order.id,
                order_number=order.order_number,
                customer_name=order.address_full_name,
                product_count=sum(item.quantity for item in matching_items),
                total_amount=round(sum(item.line_total for item in matching_items), 2),
                status=order.vendor_status,
                created_at=order.created_at,
                items=[
                    VendorOrderItemRead(
                        id=item.id,
                        product_id=item.product_id,
                        title=item.title,
                        quantity=item.quantity,
                        unit_price=item.unit_price,
                    )
                    for item in matching_items
                ],
            )
        )

    return payloads


async def submit_vendor_application(
    db: Session,
    user: User,
    *,
    business_name: str,
    business_category: str,
    business_description: str,
    phone_number: str,
    whatsapp_number: str | None,
    region: str,
    city: str,
    market_id: str | None,
    store_location: str | None,
    store_latitude: float | None,
    store_longitude: float | None,
    store_instagram_url: str | None,
    store_facebook_url: str | None,
    store_tiktok_url: str | None,
    store_twitter_url: str | None,
    store_whatsapp_url: str | None,
    store_website_url: str | None,
    store_name: str,
    store_description: str | None,
    ghana_card_number: str | None,
    business_registration_number: str | None,
    logo_image: UploadFile | None,
    banner_image: UploadFile | None,
    shop_image: UploadFile | None,
) -> VendorApplication:
    existing = get_vendor_application(db, user)
    if existing and existing.status in {
        VendorStatus.PENDING,
        VendorStatus.UNDER_REVIEW,
        VendorStatus.APPROVED,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A vendor application already exists for this account.",
        )

    get_market(db, market_id)

    if existing:
        application = existing
        application.submitted_at = datetime.now(UTC)
    else:
        application = VendorApplication(user_id=user.id)
        db.add(application)

    application.status = VendorStatus.PENDING
    application.business_name = business_name.strip()
    application.business_category = business_category.strip()
    application.business_description = business_description.strip()
    application.phone_number = phone_number.strip()
    application.whatsapp_number = whatsapp_number.strip() if whatsapp_number else None
    application.region = region.strip()
    application.city = city.strip()
    application.market_id = market_id.strip() if market_id else None
    application.store_location = store_location.strip() if store_location else None
    application.store_latitude = store_latitude
    application.store_longitude = store_longitude
    application.store_instagram_url = (
        store_instagram_url.strip() if store_instagram_url else None
    )
    application.store_facebook_url = (
        store_facebook_url.strip() if store_facebook_url else None
    )
    application.store_tiktok_url = store_tiktok_url.strip() if store_tiktok_url else None
    application.store_twitter_url = (
        store_twitter_url.strip() if store_twitter_url else None
    )
    application.store_whatsapp_url = (
        store_whatsapp_url.strip() if store_whatsapp_url else None
    )
    application.store_website_url = (
        store_website_url.strip() if store_website_url else None
    )
    application.store_name = store_name.strip()
    application.store_description = store_description.strip() if store_description else None
    application.ghana_card_number = (
        ghana_card_number.strip() if ghana_card_number else None
    )
    application.business_registration_number = (
        business_registration_number.strip()
        if business_registration_number
        else None
    )
    application.rejection_reason = None
    application.reviewed_at = None

    if logo_image:
        remove_media_file(application.logo_image_url)
        application.logo_image_url = await save_image_upload(
            logo_image,
            folder="vendors/applications/logo",
        )
    if banner_image:
        remove_media_file(application.banner_image_url)
        application.banner_image_url = await save_image_upload(
            banner_image,
            folder="vendors/applications/banner",
        )
    if shop_image:
        remove_media_file(application.shop_image_url)
        application.shop_image_url = await save_image_upload(
            shop_image,
            folder="vendors/applications/shop",
        )

    user.vendor_status = VendorStatus.PENDING
    user.vendor_rejection_reason = None
    if user.role == UserRole.VENDOR:
        user.role = UserRole.CUSTOMER

    db.commit()
    db.refresh(application)
    db.refresh(user)
    _dispatch_vendor_application_pending_email(user=user, application=application)

    return application


def fetch_my_vendor_application(db: Session, user: User) -> VendorApplication | None:
    return get_vendor_application(db, user)


def fetch_vendor_profile(db: Session, user: User) -> VendorProfileRead | None:
    application = get_vendor_application(db, user)
    if not application:
        return None

    store = get_vendor_store(db, user)
    return serialize_vendor_profile(user=user, application=application, store=store)


def fetch_vendor_dashboard(db: Session, user: User) -> VendorDashboardRead:
    require_vendor_access(user)

    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No managed store was found for this vendor.",
        )

    products = list(
        db.scalars(select(Product).where(Product.vendor_user_id == user.id)).all()
    )
    orders = list_vendor_orders_payloads(db, user)
    wallet = db.scalar(
        select(VendorWallet).where(VendorWallet.vendor_user_id == user.id)
    )

    return VendorDashboardRead(
        store_name=store.title,
        vendor_status=user.vendor_status,
        total_products=len(products),
        active_products=sum(1 for product in products if product.status == "active"),
        pending_orders=sum(
            1 for order in orders if order.status in VENDOR_ACTIVE_ORDER_STATUSES
        ),
        completed_orders=sum(1 for order in orders if order.status == "delivered"),
        total_sales=round(
            sum(
                order.total_amount
                for order in orders
                if order.status in {"confirmed", "processing", "ready", "delivered"}
            ),
            2,
        ),
        available_balance=round(wallet.available_balance, 2) if wallet else 0,
        pending_withdrawal_balance=round(wallet.pending_withdrawal_balance, 2)
        if wallet
        else 0,
        lifetime_earnings=round(wallet.lifetime_earnings, 2) if wallet else 0,
        total_commission=round(wallet.total_commission, 2) if wallet else 0,
    )


def list_vendor_products(db: Session, user: User) -> list[VendorProductRead]:
    require_vendor_access(user)
    products = list(
        db.scalars(
            select(Product)
            .where(Product.vendor_user_id == user.id)
            .order_by(Product.created_at.desc(), Product.title.asc())
        ).all()
    )
    return [serialize_vendor_product(product) for product in products]


async def create_vendor_product(
    db: Session,
    user: User,
    payload: VendorProductCreate,
    images: list[UploadFile] | None,
) -> VendorProductRead:
    require_vendor_access(user)
    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This vendor account does not have a managed store yet.",
        )

    image_urls = await save_image_uploads(images, folder="products")
    primary_image_url = image_urls[0] if image_urls else payload.image_url
    normalized_placement_tags = normalize_list(payload.placement_tags)
    (
        resolved_category,
        resolved_subcategory,
        category_slugs,
        subcategory_slugs,
    ) = resolve_product_taxonomy(
        db,
        category=payload.category,
        subcategory=payload.subcategory,
        category_slug=payload.category_slug,
    )
    product = Product(
        id=generate_product_id(),
        title=payload.name,
        description=payload.description,
        category=resolved_category,
        subcategory=resolved_subcategory,
        category_slugs=category_slugs,
        subcategory_slugs=subcategory_slugs,
        price=payload.price,
        old_price=payload.old_price,
        discount=build_discount(payload.price, payload.old_price),
        rating=None,
        reviews=None,
        image_key=payload.image_key,
        image_url=primary_image_url,
        image_urls=image_urls or payload.image_urls,
        placement_tags=normalized_placement_tags,
        color_options=normalize_list(payload.color_options),
        size_options=normalize_list(payload.size_options),
        specifications=normalize_list(payload.specifications),
        is_returnable=payload.is_returnable,
        stock=payload.stock,
        status="pending",
        store_id=store.id,
        vendor_user_id=user.id,
        audience_slug=(store.audience_slugs or [None])[0],
        section=None,
        is_active=False,
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    broadcast_catalog_product_change(product)
    serialized_product = serialize_vendor_product(product)
    realtime_manager.publish_user_event_sync(
        str(user.id),
        "vendor.product.updated",
        serialized_product.model_dump(mode="json"),
    )
    dashboard = fetch_vendor_dashboard(db, user)
    realtime_manager.publish_user_event_sync(
        str(user.id),
        "vendor.dashboard.updated",
        dashboard.model_dump(mode="json"),
    )
    return serialized_product


async def update_vendor_product(
    db: Session,
    user: User,
    product_id: str,
    payload: VendorProductUpdate,
    images: list[UploadFile] | None = None,
) -> VendorProductRead:
    require_vendor_access(user)
    product = db.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.vendor_user_id == user.id,
        )
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That vendor product was not found.",
        )

    data = payload.model_dump(exclude_unset=True)
    if "name" in data:
        data["title"] = data.pop("name")
    data.pop("status", None)

    previous_image_urls = list(product.image_urls or ([] if not product.image_url else [product.image_url]))
    next_gallery_urls = data.pop("image_urls", None)
    if next_gallery_urls is not None:
        next_gallery_urls = normalize_list(next_gallery_urls)

    next_placement_tags = data.pop("placement_tags", None)
    if next_placement_tags is not None:
        product.placement_tags = normalize_list(next_placement_tags)

    if "color_options" in data:
        product.color_options = normalize_list(data.pop("color_options"))

    if "size_options" in data:
        product.size_options = normalize_list(data.pop("size_options"))

    if "specifications" in data:
        product.specifications = normalize_list(data.pop("specifications"))

    category_slug = data.pop("category_slug", None)
    if "category" in data or "subcategory" in data or category_slug:
        next_category = data.get("category", product.category or "")
        next_subcategory = data.get("subcategory", product.subcategory)
        (
            resolved_category,
            resolved_subcategory,
            category_slugs,
            subcategory_slugs,
        ) = resolve_product_taxonomy(
            db,
            category=next_category or "",
            subcategory=next_subcategory,
            category_slug=category_slug,
        )
        product.category = resolved_category
        product.subcategory = resolved_subcategory
        product.category_slugs = category_slugs
        product.subcategory_slugs = subcategory_slugs
        data.pop("category", None)
        data.pop("subcategory", None)

    for key, value in data.items():
        setattr(product, key, value)

    uploaded_image_urls = await save_image_uploads(images, folder="products")
    if next_gallery_urls is not None or uploaded_image_urls:
        merged_gallery = (next_gallery_urls or []) + uploaded_image_urls
        product.image_urls = merged_gallery or None
        product.image_url = merged_gallery[0] if merged_gallery else None
        if not merged_gallery and "image_url" in data:
            product.image_url = data["image_url"]

        removed_urls = [
            url for url in previous_image_urls if url and url not in (merged_gallery or [])
        ]
        for removed_url in removed_urls:
            remove_media_file(removed_url)

    product.discount = build_discount(product.price, product.old_price)
    if product.store_id:
        store = db.scalar(select(Store).where(Store.id == product.store_id))
        if store:
            product.audience_slug = (store.audience_slugs or [product.audience_slug])[0]

    if product.status != "suspended":
        product.status = "pending"
    product.is_active = False

    db.commit()
    db.refresh(product)
    broadcast_catalog_product_change(product)
    serialized_product = serialize_vendor_product(product)
    realtime_manager.publish_user_event_sync(
        str(user.id),
        "vendor.product.updated",
        serialized_product.model_dump(mode="json"),
    )
    dashboard = fetch_vendor_dashboard(db, user)
    realtime_manager.publish_user_event_sync(
        str(user.id),
        "vendor.dashboard.updated",
        dashboard.model_dump(mode="json"),
    )
    return serialized_product


def delete_vendor_product(db: Session, user: User, product_id: str) -> None:
    require_vendor_access(user)
    product = db.scalar(
        select(Product).where(
            Product.id == product_id,
            Product.vendor_user_id == user.id,
        )
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That vendor product was not found.",
        )

    deleted_store_id = product.store_id
    deleted_category = product.category
    deleted_subcategory = product.subcategory
    deleted_audience_slug = product.audience_slug
    deleted_section = product.section
    db.delete(product)
    db.commit()
    from app.core.cache import invalidate_catalog_product

    invalidate_catalog_product(product_id)
    realtime_manager.broadcast_event_sync(
        "catalog.product.changed",
        {
            "product_id": product_id,
            "store_id": deleted_store_id,
            "status": "deleted",
            "is_active": False,
            "category": deleted_category,
            "subcategory": deleted_subcategory,
            "audience_slug": deleted_audience_slug,
            "section": deleted_section,
        },
    )


def list_vendor_orders(db: Session, user: User) -> list[VendorOrderRead]:
    require_vendor_access(user)
    return list_vendor_orders_payloads(db, user)


def _vendor_voucher_stats_map(
    db: Session,
    voucher_ids: list[uuid.UUID],
) -> dict[uuid.UUID, dict[str, float | int]]:
    if not voucher_ids:
        return {}

    rows = db.execute(
        select(
            VoucherRedemption.voucher_id,
            func.count(VoucherRedemption.id),
            func.count(func.distinct(VoucherRedemption.user_id)),
            func.coalesce(func.sum(VoucherRedemption.discount_amount), 0),
        )
        .where(VoucherRedemption.voucher_id.in_(voucher_ids))
        .group_by(VoucherRedemption.voucher_id)
    ).all()
    return {
        voucher_id: {
            "redemption_count": int(redemption_count),
            "unique_user_count": int(unique_user_count),
            "total_discount_amount": float(total_discount_amount or 0),
        }
        for voucher_id, redemption_count, unique_user_count, total_discount_amount in rows
    }


def _serialize_vendor_voucher(
    voucher: Voucher,
    *,
    redemption_count: int = 0,
    unique_user_count: int = 0,
    total_discount_amount: float = 0,
) -> VendorVoucherRead:
    return VendorVoucherRead(
        id=voucher.id,
        code=voucher.code,
        title=voucher.title,
        description=voucher.description,
        issuer_name=voucher.issuer_name,
        availability=voucher.availability,
        reward_text=voucher.reward_text,
        discount_type=voucher.discount_type,
        discount_value=round(voucher.discount_value, 2),
        min_subtotal=round(voucher.min_subtotal, 2),
        max_discount=round(voucher.max_discount, 2) if voucher.max_discount is not None else None,
        usage_limit=voucher.usage_limit,
        per_user_limit=voucher.per_user_limit,
        is_active=voucher.is_active,
        status=voucher_status(
            voucher,
            now=datetime.now(UTC),
            overall_count=redemption_count,
        ),
        redemption_count=redemption_count,
        unique_user_count=unique_user_count,
        total_discount_amount=round(total_discount_amount, 2),
        starts_at=voucher.starts_at,
        ends_at=voucher.ends_at,
        created_at=voucher.created_at,
    )


def _get_vendor_voucher(db: Session, user: User, voucher_id: str) -> Voucher:
    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No managed store was found for this vendor.",
        )

    try:
        normalized_id = uuid.UUID(str(voucher_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That store promotion was not found.",
        ) from exc

    voucher = db.scalar(
        select(Voucher).where(
            Voucher.id == normalized_id,
            Voucher.scope == "store",
            Voucher.store_id == store.id,
        )
    )
    if not voucher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That store promotion was not found.",
        )
    return voucher


def update_vendor_order_status(
    db: Session,
    user: User,
    order_id: str,
    payload: VendorOrderStatusUpdate,
) -> VendorOrderRead:
    require_vendor_access(user)
    next_status = payload.status.strip().lower()
    if next_status not in VENDOR_ALLOWED_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That vendor status is not supported.",
        )

    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.user))
        .where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found.",
        )

    matching_items = []
    for item in order.items:
        owns_item = item.vendor_user_id == user.id
        if not owns_item:
            owns_item = db.scalar(
                select(Product.id).where(
                    Product.id == item.product_id,
                    Product.vendor_user_id == user.id,
                )
            )
        if owns_item:
            matching_items.append(item)

    if not matching_items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found for this vendor.",
        )

    order.vendor_status = next_status
    changed_wallet_vendor_ids: set[uuid.UUID] = set()
    if next_status == "delivered":
        order.status = "delivered"
        order.progress = 1
        order.tracking_eta = None
        order.delivered_at = datetime.now(UTC)
        order.cancelled_at = None
        order.cancellation_reason = None
        changed_wallet_vendor_ids = settle_vendor_wallets_for_order(
            db,
            order,
            vendor_scope={user.id},
        )
    elif next_status == "cancelled":
        order.status = "cancelled"
        order.progress = 0
        order.tracking_eta = None
        order.cancelled_at = datetime.now(UTC)
        order.cancellation_reason = "Cancelled by store"
    else:
        order.status = "processing"
        progress_map = {
            "pending": 0.1,
            "confirmed": 0.2,
            "processing": 0.45,
            "ready": 0.75,
        }
        eta_map = {
            "pending": "Awaiting vendor confirmation",
            "confirmed": "Confirmed by vendor",
            "processing": "Being prepared for dispatch",
            "ready": "Ready for delivery handoff",
        }
        order.progress = progress_map.get(next_status, order.progress)
        order.tracking_eta = eta_map.get(next_status, order.tracking_eta)
        order.cancelled_at = None
        order.cancellation_reason = None

    create_notification_event(
        db,
        order.user,
        kind="vendor_order_update",
        title="Order update from your store",
        body=f"Order #{order.order_number} is now {next_status.replace('_', ' ')}.",
        icon="bag-handle-outline",
        accent="neutral" if next_status != "cancelled" else "warning",
        action_label="Track order",
        route_type="order",
        route_target_id=str(order.id),
        image_key=matching_items[0].image_key if matching_items else None,
    )
    db.commit()
    db.refresh(order)
    for vendor_user_id in changed_wallet_vendor_ids:
        publish_vendor_wallet_updates(vendor_user_id)
    realtime_manager.publish_user_event_sync(
        str(order.user_id),
        "order.updated",
        OrderRead.model_validate(order).model_dump(mode="json"),
    )

    vendor_orders = list_vendor_orders_payloads(db, user)
    for vendor_order in vendor_orders:
        if str(vendor_order.id) == order_id:
            realtime_manager.publish_user_event_sync(
                str(user.id),
                "vendor.order.updated",
                vendor_order.model_dump(mode="json"),
            )
            dashboard = fetch_vendor_dashboard(db, user)
            realtime_manager.publish_user_event_sync(
                str(user.id),
                "vendor.dashboard.updated",
                dashboard.model_dump(mode="json"),
            )
            return vendor_order

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="That order was not found for this vendor.",
    )


def list_vendor_vouchers(db: Session, user: User) -> list[VendorVoucherRead]:
    require_vendor_access(user)
    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No managed store was found for this vendor.",
        )

    vouchers = list(
        db.scalars(
            select(Voucher)
            .where(
                Voucher.scope == "store",
                Voucher.store_id == store.id,
            )
            .order_by(Voucher.created_at.desc(), Voucher.title.asc())
        ).all()
    )
    stats_map = _vendor_voucher_stats_map(db, [voucher.id for voucher in vouchers])
    return [
        _serialize_vendor_voucher(
            voucher,
            redemption_count=int(stats_map.get(voucher.id, {}).get("redemption_count", 0)),
            unique_user_count=int(stats_map.get(voucher.id, {}).get("unique_user_count", 0)),
            total_discount_amount=float(
                stats_map.get(voucher.id, {}).get("total_discount_amount", 0)
            ),
        )
        for voucher in vouchers
    ]


def create_vendor_voucher(
    db: Session,
    user: User,
    payload: VendorVoucherUpsert,
) -> VendorVoucherRead:
    require_vendor_access(user)
    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No managed store was found for this vendor.",
        )

    validate_voucher_configuration(
        scope="store",
        availability=payload.availability,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        usage_limit=payload.usage_limit,
        per_user_limit=payload.per_user_limit,
        store_id=store.id,
    )
    discount_value = 0 if payload.discount_type == "free_shipping" else round(payload.discount_value, 2)
    voucher = Voucher(
        code=payload.code,
        title=payload.title,
        description=payload.description,
        issuer_name=payload.issuer_name or store.title,
        scope="store",
        availability=payload.availability,
        store_id=store.id,
        reward_text=build_voucher_reward_text(payload.discount_type, discount_value),
        discount_type=payload.discount_type,
        discount_value=discount_value,
        min_subtotal=round(payload.min_subtotal, 2),
        max_discount=round(payload.max_discount, 2) if payload.max_discount is not None else None,
        usage_limit=payload.usage_limit,
        per_user_limit=payload.per_user_limit,
        is_active=payload.is_active,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )
    db.add(voucher)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That voucher code already exists.",
        ) from exc
    db.refresh(voucher)
    return _serialize_vendor_voucher(voucher)


def update_vendor_voucher(
    db: Session,
    user: User,
    voucher_id: str,
    payload: VendorVoucherUpsert,
) -> VendorVoucherRead:
    require_vendor_access(user)
    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No managed store was found for this vendor.",
        )

    validate_voucher_configuration(
        scope="store",
        availability=payload.availability,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        usage_limit=payload.usage_limit,
        per_user_limit=payload.per_user_limit,
        store_id=store.id,
    )
    voucher = _get_vendor_voucher(db, user, voucher_id)
    discount_value = 0 if payload.discount_type == "free_shipping" else round(payload.discount_value, 2)
    voucher.code = payload.code
    voucher.title = payload.title
    voucher.description = payload.description
    voucher.issuer_name = payload.issuer_name or store.title
    voucher.availability = payload.availability
    voucher.reward_text = build_voucher_reward_text(payload.discount_type, discount_value)
    voucher.discount_type = payload.discount_type
    voucher.discount_value = discount_value
    voucher.min_subtotal = round(payload.min_subtotal, 2)
    voucher.max_discount = round(payload.max_discount, 2) if payload.max_discount is not None else None
    voucher.usage_limit = payload.usage_limit
    voucher.per_user_limit = payload.per_user_limit
    voucher.is_active = payload.is_active
    voucher.starts_at = payload.starts_at
    voucher.ends_at = payload.ends_at

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That voucher code already exists.",
        ) from exc

    db.refresh(voucher)
    stats_map = _vendor_voucher_stats_map(db, [voucher.id])
    stats = stats_map.get(voucher.id, {})
    return _serialize_vendor_voucher(
        voucher,
        redemption_count=int(stats.get("redemption_count", 0)),
        unique_user_count=int(stats.get("unique_user_count", 0)),
        total_discount_amount=float(stats.get("total_discount_amount", 0)),
    )


def archive_vendor_voucher(db: Session, user: User, voucher_id: str) -> None:
    require_vendor_access(user)
    voucher = _get_vendor_voucher(db, user, voucher_id)
    voucher.is_active = False
    db.commit()


def gift_vendor_voucher(
    db: Session,
    user: User,
    voucher_id: str,
    payload: VendorVoucherGiftPayload,
) -> VendorVoucherRead:
    require_vendor_access(user)
    voucher = _get_vendor_voucher(db, user, voucher_id)
    recipient = db.scalar(select(User).where(User.email == payload.recipient_email))
    if not recipient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That shopper account was not found.",
        )
    if not recipient.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That shopper account is not currently active.",
        )

    assign_voucher_to_user(
        db,
        voucher=voucher,
        recipient=recipient,
        source="gift",
        assigned_by_user_id=user.id,
        note=payload.note,
    )

    stats_map = _vendor_voucher_stats_map(db, [voucher.id])
    stats = stats_map.get(voucher.id, {})
    return _serialize_vendor_voucher(
        voucher,
        redemption_count=int(stats.get("redemption_count", 0)),
        unique_user_count=int(stats.get("unique_user_count", 0)),
        total_discount_amount=float(stats.get("total_discount_amount", 0)),
    )


def fetch_vendor_store(db: Session, user: User) -> VendorStoreRead:
    require_vendor_access(user)
    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No managed store was found for this vendor.",
        )

    return serialize_vendor_store(store)


async def update_vendor_store(
    db: Session,
    user: User,
    *,
    name: str,
    description: str,
    category: str,
    audience_slugs: list[str] | None,
    market_id: str | None,
    location: str | None,
    phone: str | None,
    latitude: float | None,
    longitude: float | None,
    instagram_url: str | None,
    facebook_url: str | None,
    tiktok_url: str | None,
    twitter_url: str | None,
    whatsapp_url: str | None,
    website_url: str | None,
    region: str,
    city: str,
    logo_image: UploadFile | None,
    banner_image: UploadFile | None,
) -> VendorStoreRead:
    require_vendor_access(user)
    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No managed store was found for this vendor.",
        )

    market = get_market(db, market_id)
    store.title = name.strip()
    store.slug = build_unique_store_slug(db, store.title, store.id)
    store.description = description.strip()
    store.category = category.strip()
    store.audience_slugs = normalize_list(audience_slugs)
    store.market_id = market.id if market else None
    store.market_slug = market.slug if market else None
    store.address = location.strip() if location else None
    store.phone = phone.strip() if phone else None
    store.latitude = latitude
    store.longitude = longitude
    store.instagram_url = instagram_url.strip() if instagram_url else None
    store.facebook_url = facebook_url.strip() if facebook_url else None
    store.tiktok_url = tiktok_url.strip() if tiktok_url else None
    store.twitter_url = twitter_url.strip() if twitter_url else None
    store.whatsapp_url = whatsapp_url.strip() if whatsapp_url else None
    store.website_url = website_url.strip() if website_url else None
    store.region = region.strip()
    store.city = city.strip()

    if logo_image:
        remove_media_file(store.image_url)
        store.image_url = await save_image_upload(logo_image, folder="stores/logo")
    if banner_image:
        remove_media_file(store.image_banner_url)
        store.image_banner_url = await save_image_upload(
            banner_image,
            folder="stores/banner",
        )

    db.commit()
    db.refresh(store)
    broadcast_catalog_store_change(store)
    return serialize_vendor_store(store)


def list_vendor_applications(db: Session, user: User) -> list[VendorApplicationListItem]:
    require_admin(user)
    applications = list(
        db.scalars(
            select(VendorApplication)
            .options(selectinload(VendorApplication.user))
            .order_by(
                VendorApplication.submitted_at.desc(),
                VendorApplication.created_at.desc(),
            )
        ).all()
    )
    return [
        VendorApplicationListItem(
            **VendorApplicationRead.model_validate(application).model_dump(),
            full_name=application.user.full_name,
            email=application.user.email,
        )
        for application in applications
    ]


def approve_vendor_application(
    db: Session,
    user: User,
    application_id: str,
) -> VendorApplication:
    require_admin(user)
    application = db.scalar(
        select(VendorApplication)
        .options(selectinload(VendorApplication.user))
        .where(VendorApplication.id == application_id)
    )
    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That vendor application was not found.",
        )

    market = get_market(db, application.market_id)
    applicant = application.user
    store = get_vendor_store(db, applicant)
    if not store:
        store = Store(
            id=generate_store_id(),
            slug=build_unique_store_slug(db, application.store_name),
            title=application.store_name,
            category=application.business_category,
            market_id=market.id if market else None,
            market_slug=market.slug if market else None,
            image_key="bag",
            image_url=application.logo_image_url,
            image_banner_key="ladiesstore",
            image_banner_url=application.banner_image_url or application.shop_image_url,
            address=application.store_location,
            latitude=application.store_latitude,
            longitude=application.store_longitude,
            instagram_url=application.store_instagram_url,
            facebook_url=application.store_facebook_url,
            tiktok_url=application.store_tiktok_url,
            twitter_url=application.store_twitter_url,
            whatsapp_url=application.store_whatsapp_url or application.whatsapp_number,
            website_url=application.store_website_url,
            phone=application.phone_number,
            email=applicant.email,
            city=application.city,
            region=application.region,
            description=application.store_description
            or application.business_description[:255],
            rating=0,
            status="active",
            vendor_user_id=applicant.id,
        )
        db.add(store)
    else:
        store.title = application.store_name
        store.slug = build_unique_store_slug(db, store.title, store.id)
        store.category = application.business_category
        store.market_id = market.id if market else None
        store.market_slug = market.slug if market else None
        store.image_url = application.logo_image_url or store.image_url
        store.image_banner_url = (
            application.banner_image_url
            or application.shop_image_url
            or store.image_banner_url
        )
        store.address = application.store_location
        store.latitude = application.store_latitude
        store.longitude = application.store_longitude
        store.instagram_url = application.store_instagram_url
        store.facebook_url = application.store_facebook_url
        store.tiktok_url = application.store_tiktok_url
        store.twitter_url = application.store_twitter_url
        store.whatsapp_url = application.store_whatsapp_url or application.whatsapp_number
        store.website_url = application.store_website_url
        store.phone = application.phone_number
        store.email = applicant.email
        store.city = application.city
        store.region = application.region
        store.description = (
            application.store_description or application.business_description[:255]
        )
        store.vendor_user_id = applicant.id
        store.status = "active"

    application.status = VendorStatus.APPROVED
    application.reviewed_at = datetime.now(UTC)
    application.rejection_reason = None
    applicant.vendor_status = VendorStatus.APPROVED
    applicant.vendor_rejection_reason = None
    if applicant.role != UserRole.ADMIN:
        applicant.role = UserRole.VENDOR

    create_notification_event(
        db,
        applicant,
        kind="vendor_approved",
        title="Vendor application approved",
        body=f"{application.store_name} is now live for vendor management in ODOS.",
        icon="checkmark-done-outline",
        accent="success",
        action_label="Open dashboard",
        route_type="profile",
        route_target_id=str(applicant.id),
        image_key="bag",
    )
    db.commit()
    db.refresh(application)
    if store:
        db.refresh(store)
        broadcast_catalog_store_change(store)
    _dispatch_vendor_application_approved_email(user=applicant, application=application)
    return application


def reject_vendor_application(
    db: Session,
    user: User,
    application_id: str,
    rejection_reason: str | None,
) -> VendorApplication:
    require_admin(user)
    application = db.scalar(
        select(VendorApplication)
        .options(selectinload(VendorApplication.user))
        .where(VendorApplication.id == application_id)
    )
    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That vendor application was not found.",
        )

    reason = rejection_reason or "Please review the store information and apply again."
    application.status = VendorStatus.REJECTED
    application.reviewed_at = datetime.now(UTC)
    application.rejection_reason = reason
    application.user.vendor_status = VendorStatus.REJECTED
    application.user.vendor_rejection_reason = reason
    if application.user.role == UserRole.VENDOR:
        application.user.role = UserRole.CUSTOMER

    create_notification_event(
        db,
        application.user,
        kind="vendor_rejected",
        title="Vendor application needs changes",
        body=reason,
        icon="close-circle-outline",
        accent="warning",
        action_label="Review status",
        route_type="profile",
        route_target_id=str(application.user.id),
        image_key="bag",
    )
    db.commit()
    db.refresh(application)
    return application
