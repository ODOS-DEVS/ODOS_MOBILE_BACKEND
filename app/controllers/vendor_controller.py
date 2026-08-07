import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.admin_pagination import normalize_page_params, paginate_scalars
from app.schemas.pagination import AdminPageRead
from app.controllers.notification_controller import create_notification_event, order_notification_image
from app.services.push_service import (
    customer_order_status_push_copy,
    dispatch_customer_order_push,
)
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
    NotificationEvent,
    Order,
    OrderItem,
    Product,
    ReturnRequest,
    Review,
    Store,
    User,
    UserRole,
    VendorApplication,
    VendorStatus,
    VendorWallet,
    VendorWalletTransaction,
    Voucher,
    VoucherRedemption,
)
from app.services.inventory_service import LOW_STOCK_THRESHOLD
from app.services.finance_math import vendor_allocation_map
from app.schemas.vendor import (
    VendorAnalyticsDailyPoint,
    VendorAnalyticsRead,
    VendorApplicationListItem,
    VendorApplicationRead,
    VendorCustomerRead,
    VendorDashboardRead,
    VendorOrderItemRead,
    VendorOrderRead,
    VendorOrderStatusUpdate,
    VendorReturnRequestRead,
    VendorReviewReplyUpdate,
    VendorReviewRead,
    VendorTopProductRead,
    VendorProductCreate,
    VendorProductRead,
    VendorInventoryMovementRead,
    VendorProductUpdate,
    VendorProfileRead,
    VendorStoreRead,
    VendorVoucherGiftPayload,
    VendorVoucherRead,
    VendorVoucherRedemptionRead,
    VendorVoucherUpsert,
)
from app.schemas.order import OrderRead, OrderStatusEventRead
from app.core.admin_permissions import list_admins_with_feature
from app.core.config import settings
from app.services.email_service import (
    send_admin_vendor_application_email,
    send_admin_voucher_review_email,
    send_vendor_application_approved_email,
    send_vendor_application_pending_email,
)
from app.services.media_service import remove_media_file, save_image_upload, save_image_uploads
from app.services.realtime_service import realtime_manager
from app.services.delivery_service import (
    get_delivery_config,
    tracking_eta_for_vendor_status,
)
from app.services.order_timeline_service import (
    ensure_delivery_code,
    record_order_status_event,
)
from app.services.sms_service import send_delivery_out_for_delivery_sms

logger = logging.getLogger(__name__)
VENDOR_ACTIVE_ORDER_STATUSES = {"pending", "confirmed", "processing", "ready", "out_for_delivery"}
VENDOR_OPEN_RETURN_STATUSES = {"requested", "under_review", "approved"}
# Canonical countable sales statuses for dashboard + analytics (keep in sync).
VENDOR_SALES_ORDER_STATUSES = {
    "confirmed",
    "processing",
    "ready",
    "out_for_delivery",
    "delivered",
}
VENDOR_ANALYTICS_ORDER_STATUSES = VENDOR_SALES_ORDER_STATUSES
VENDOR_CANCELLED_ORDER_STATUSES = {"cancelled", "canceled", "refunded"}
VENDOR_ALLOWED_STATUSES = {
    "pending",
    "confirmed",
    "processing",
    "ready",
    "out_for_delivery",
    "delivered",
    "cancelled",
}
# Mirrors the mobile client's VENDOR_ORDER_NEXT_STATUS (utils/vendorOrderFulfillment.ts) —
# the app only ever offers a single "mark next stage" button, so the API should refuse
# anything the UI itself would never send (skips, reversals, or PATCHing a stale state).
VENDOR_STATUS_FORWARD_TRANSITIONS = {
    "pending": "confirmed",
    "confirmed": "processing",
    "processing": "ready",
    "ready": "out_for_delivery",
    "out_for_delivery": "delivered",
}
# Mirrors canCancelVendorOrder on mobile — cancellation is only offered before prep starts.
VENDOR_STATUS_CANCELLABLE_FROM = {"pending", "confirmed"}


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


def _dispatch_admin_vendor_application_alert(
    db: Session,
    *,
    user: User,
    application: VendorApplication,
) -> None:
    admins = list_admins_with_feature(db, "vendors")
    for admin in admins:
        if not admin.email:
            continue
        try:
            send_admin_vendor_application_email(
                to_email=admin.email,
                to_name=admin.full_name,
                store_name=application.store_name,
                business_category=application.business_category,
                applicant_name=user.full_name or user.email,
                city=application.city,
                region=application.region,
                submitted_at_label=(
                    application.submitted_at or application.created_at or datetime.now(UTC)
                ).strftime("%d %b %Y, %I:%M %p UTC"),
                application_id=str(application.id),
                admin_panel_url=settings.admin_panel_url,
            )
        except Exception:
            logger.exception(
                "Failed to send admin vendor-application alert to %s",
                admin.email,
            )


def _dispatch_admin_voucher_review_alert(
    db: Session,
    *,
    voucher: Voucher,
    store_title: str,
) -> None:
    admins = list_admins_with_feature(db, "promotions")
    for admin in admins:
        if not admin.email:
            continue
        try:
            send_admin_voucher_review_email(
                to_email=admin.email,
                to_name=admin.full_name,
                store_name=store_title,
                voucher_code=voucher.code,
                voucher_title=voucher.title,
                reward_text=voucher.reward_text or "—",
                submitted_at_label=datetime.now(UTC).strftime("%d %b %Y, %I:%M %p UTC"),
                voucher_id=str(voucher.id),
                admin_panel_url=settings.admin_panel_url,
            )
        except Exception:
            logger.exception(
                "Failed to send admin voucher-review alert to %s",
                admin.email,
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
        is_on_vacation=store.is_on_vacation,
        vacation_message=store.vacation_message,
        business_hours=store.business_hours,
    )


def serialize_vendor_product(
    product: Product,
    *,
    reserved_stock: int | None = None,
) -> VendorProductRead:
    on_hand = int(product.stock or 0)
    reserved = int(reserved_stock or 0)
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
        stock=on_hand,
        reserved_stock=reserved,
        available_stock=max(0, on_hand - reserved),
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
            "is_on_vacation": store.is_on_vacation,
        },
    )


def _matching_vendor_items(db: Session, user: User, order: Order) -> list:
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
    return matching_items


def _order_earnings_fields(db: Session, user: User, order: Order) -> dict[str, float | bool | str | None]:
    allocation = vendor_allocation_map(order, vendor_scope={user.id}).get(user.id)
    if not allocation:
        return {
            "gross_amount": None,
            "commission_amount": None,
            "net_amount": None,
            "is_settled": False,
            "currency": "GHS",
        }

    is_settled = bool(
        db.scalar(
            select(VendorWalletTransaction.id).where(
                VendorWalletTransaction.vendor_user_id == user.id,
                VendorWalletTransaction.order_id == order.id,
                VendorWalletTransaction.kind == "sale_settlement",
            )
        )
    )
    return {
        "gross_amount": allocation["gross_amount"],
        "commission_amount": allocation["commission_amount"],
        "net_amount": allocation["net_amount"],
        "is_settled": is_settled,
        "currency": "GHS",
    }


def _serialize_vendor_order(db: Session, user: User, order: Order) -> VendorOrderRead | None:
    matching_items = _matching_vendor_items(db, user, order)
    if not matching_items:
        return None

    earnings = _order_earnings_fields(db, user, order)

    return VendorOrderRead(
        id=order.id,
        order_number=order.order_number,
        customer_name=order.address_full_name,
        customer_phone=order.address_phone,
        delivery_method=order.delivery_method,
        address_street=order.address_street,
        address_city=order.address_city,
        address_region=order.address_region,
        payment_label=order.payment_label,
        product_count=sum(item.quantity for item in matching_items),
        total_amount=round(sum(item.line_total for item in matching_items), 2),
        gross_amount=earnings["gross_amount"],
        commission_amount=earnings["commission_amount"],
        net_amount=earnings["net_amount"],
        is_settled=bool(earnings["is_settled"]),
        currency=str(earnings["currency"]),
        status=order.vendor_status,
        delivery_code=order.delivery_code,
        delivery_instructions=order.delivery_instructions,
        reschedule_requested_at=order.reschedule_requested_at,
        reschedule_note=order.reschedule_note,
        dispatch_photo_url=order.dispatch_photo_url,
        departure_notified_at=order.departure_notified_at,
        placed_at=order.placed_at,
        paid_at=order.paid_at,
        created_at=order.created_at,
        timeline=[
            OrderStatusEventRead.model_validate(event) for event in order.timeline
        ],
        items=[
            VendorOrderItemRead(
                id=item.id,
                product_id=item.product_id,
                title=item.title,
                quantity=item.quantity,
                unit_price=item.unit_price,
                image_url=item.image_url,
            )
            for item in matching_items
        ],
    )


def _serialize_vendor_return_request(request: ReturnRequest) -> VendorReturnRequestRead:
    order = request.order
    order_item = request.order_item
    return VendorReturnRequestRead(
        id=request.id,
        order_id=request.order_id,
        order_number=order.order_number,
        order_item_id=request.order_item_id,
        product_id=order_item.product_id,
        product_title=order_item.title,
        product_image_url=order_item.image_url,
        customer_name=order.address_full_name,
        request_type=request.request_type,
        status=request.status,
        quantity=request.quantity,
        reason=request.reason,
        details=request.details,
        evidence_image_urls=request.evidence_image_urls,
        admin_note=request.admin_note,
        refund_amount=round(request.refund_amount, 2)
        if request.refund_amount is not None
        else None,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


def list_vendor_orders_payloads(db: Session, user: User) -> list[VendorOrderRead]:
    order_ids = list(
        db.scalars(
            select(OrderItem.order_id)
            .where(OrderItem.vendor_user_id == user.id)
            .distinct()
        ).all()
    )
    if not order_ids:
        return []

    orders = list(
        db.scalars(
            select(Order)
            .options(selectinload(Order.items), selectinload(Order.user), selectinload(Order.timeline))
            .where(Order.id.in_(order_ids))
            .order_by(Order.placed_at.desc(), Order.created_at.desc())
        ).all()
    )

    payloads: list[VendorOrderRead] = []
    for order in orders:
        payload = _serialize_vendor_order(db, user, order)
        if payload:
            payloads.append(payload)

    return payloads


def get_vendor_order(db: Session, user: User, order_id: str) -> VendorOrderRead:
    require_vendor_access(user)
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items), selectinload(Order.user), selectinload(Order.timeline))
        .where(Order.id == order_id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found.",
        )

    payload = _serialize_vendor_order(db, user, order)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found for this vendor.",
        )
    return payload


def acknowledge_vendor_order(db: Session, user: User, order_id: str) -> VendorOrderRead:
    payload = get_vendor_order(db, user, order_id)
    existing = db.scalar(
        select(NotificationEvent.id).where(
            NotificationEvent.user_id == user.id,
            NotificationEvent.kind == "vendor_order_acknowledged",
            NotificationEvent.route_target_id == str(payload.id),
        )
    )
    if not existing:
        create_notification_event(
            db,
            user,
            kind="vendor_order_acknowledged",
            title="Order acknowledged",
            body=f"You acknowledged order #{payload.order_number}.",
            icon="checkmark-circle-outline",
            accent="success",
            action_label="View order",
            route_type="vendor_order",
            route_target_id=str(payload.id),
        )
        db.commit()
    return payload


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
    _dispatch_admin_vendor_application_alert(db, user=user, application=application)

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

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_orders = [
        order
        for order in orders
        if (order.placed_at or order.created_at) >= today_start
        and order.status in VENDOR_SALES_ORDER_STATUSES
    ]

    review_stats = db.execute(
        select(func.count(Review.id), func.avg(Review.rating))
        .join(Product, Product.id == Review.product_id)
        .where(
            Product.vendor_user_id == user.id,
            Review.is_hidden.is_(False),
        )
    ).one()
    review_count = int(review_stats[0] or 0)
    avg_rating = round(float(review_stats[1]), 2) if review_stats[1] is not None else None

    customer_keys = {
        (order.customer_phone or "").strip() or (order.customer_name or "").strip().lower()
        for order in orders
        if order.status not in VENDOR_CANCELLED_ORDER_STATUSES
        and (order.customer_phone or order.customer_name)
    }
    customer_keys.discard("")

    active_voucher_count = int(
        db.scalar(
            select(func.count(Voucher.id)).where(
                Voucher.scope == "store",
                Voucher.store_id == store.id,
                Voucher.owner_type == "vendor",
                Voucher.is_active.is_(True),
                Voucher.approval_status == "approved",
            )
        )
        or 0
    )

    return VendorDashboardRead(
        store_name=store.title,
        vendor_status=user.vendor_status,
        active_voucher_count=active_voucher_count,
        is_on_vacation=bool(store.is_on_vacation),
        total_products=len(products),
        active_products=sum(1 for product in products if product.status == "active"),
        pending_orders=sum(
            1 for order in orders if order.status in VENDOR_ACTIVE_ORDER_STATUSES
        ),
        processing_orders=sum(
            1 for order in orders if order.status in {"confirmed", "processing", "ready"}
        ),
        completed_orders=sum(1 for order in orders if order.status == "delivered"),
        cancelled_orders=sum(
            1 for order in orders if order.status in VENDOR_CANCELLED_ORDER_STATUSES
        ),
        total_sales=round(
            sum(
                order.total_amount
                for order in orders
                if order.status in VENDOR_SALES_ORDER_STATUSES
            ),
            2,
        ),
        today_sales=round(sum(order.total_amount for order in today_orders), 2),
        today_orders=len(today_orders),
        low_stock_count=sum(
            1
            for product in products
            if 0 < int(product.stock or 0) <= LOW_STOCK_THRESHOLD
        ),
        out_of_stock_count=sum(1 for product in products if int(product.stock or 0) <= 0),
        avg_rating=avg_rating,
        review_count=review_count,
        customer_count=len(customer_keys),
        available_balance=round(wallet.available_balance, 2) if wallet else 0,
        pending_withdrawal_balance=round(wallet.pending_withdrawal_balance, 2)
        if wallet
        else 0,
        lifetime_earnings=round(wallet.lifetime_earnings, 2) if wallet else 0,
        total_commission=round(wallet.total_commission, 2) if wallet else 0,
    )


def _serialize_vendor_review(review: Review, product: Product, customer: User) -> VendorReviewRead:
    image_url = None
    if product.image_url:
        image_url = product.image_url
    elif product.image_urls:
        image_url = product.image_urls[0] if product.image_urls else None
    return VendorReviewRead(
        id=review.id,
        product_id=product.id,
        product_title=product.title,
        product_image_url=image_url,
        rating=float(review.rating),
        comment=review.comment,
        customer_name=customer.full_name,
        is_hidden=bool(review.is_hidden),
        vendor_reply=review.vendor_reply,
        vendor_replied_at=review.vendor_replied_at,
        created_at=review.created_at,
    )


def list_vendor_reviews(
    db: Session,
    user: User,
    *,
    q: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[VendorReviewRead]:
    require_vendor_access(user)
    resolved_limit, resolved_offset = normalize_page_params(limit, offset)

    statement = (
        select(Review, Product, User)
        .join(Product, Product.id == Review.product_id)
        .join(User, User.id == Review.user_id)
        .where(Product.vendor_user_id == user.id)
    )

    cleaned_query = (q or "").strip()
    if cleaned_query:
        pattern = f"%{cleaned_query}%"
        statement = statement.where(
            or_(
                Product.title.ilike(pattern),
                Review.comment.ilike(pattern),
                User.full_name.ilike(pattern),
            )
        )

    statement = (
        statement.order_by(Review.created_at.desc())
        .offset(resolved_offset)
        .limit(resolved_limit)
    )
    rows = db.execute(statement).all()

    return [
        _serialize_vendor_review(review, product, customer)
        for review, product, customer in rows
    ]


def reply_to_vendor_review(
    db: Session,
    user: User,
    review_id: str,
    payload: VendorReviewReplyUpdate,
) -> VendorReviewRead:
    require_vendor_access(user)
    try:
        normalized_review_id = uuid.UUID(str(review_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That review was not found for this vendor.",
        ) from exc

    row = db.execute(
        select(Review, Product, User)
        .join(Product, Product.id == Review.product_id)
        .join(User, User.id == Review.user_id)
        .where(
            Review.id == normalized_review_id,
            Product.vendor_user_id == user.id,
        )
    ).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That review was not found for this vendor.",
        )

    review, product, customer = row
    review.vendor_reply = payload.reply
    review.vendor_replied_at = datetime.now(UTC)
    db.commit()
    db.refresh(review)
    return _serialize_vendor_review(review, product, customer)


def list_vendor_customers(
    db: Session,
    user: User,
    *,
    q: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[VendorCustomerRead]:
    require_vendor_access(user)
    orders = list_vendor_orders_payloads(db, user)
    buckets: dict[str, VendorCustomerRead] = {}
    epoch = datetime(1970, 1, 1, tzinfo=UTC)

    for order in orders:
        if order.status in VENDOR_CANCELLED_ORDER_STATUSES:
            continue

        phone = (order.customer_phone or "").strip()
        name = (order.customer_name or "").strip() or "Customer"
        # Prefer phone; fall back to name only when phone is missing.
        key = phone or f"name:{(name or 'customer').lower()}"
        if not key:
            continue

        existing = buckets.get(key)
        placed_at = order.placed_at or order.created_at
        spent = float(order.total_amount or 0)
        if existing is None:
            buckets[key] = VendorCustomerRead(
                customer_key=key,
                customer_name=name,
                customer_phone=phone or None,
                order_count=1,
                total_spent=round(spent, 2),
                last_order_at=placed_at,
                currency=order.currency or "GHS",
            )
            continue

        existing.order_count += 1
        existing.total_spent = round(existing.total_spent + spent, 2)
        if placed_at and (
            existing.last_order_at is None or placed_at > existing.last_order_at
        ):
            existing.last_order_at = placed_at
            existing.customer_name = name
            if phone:
                existing.customer_phone = phone

    def sort_key(item: VendorCustomerRead) -> tuple[datetime, float]:
        last = item.last_order_at
        if last is None:
            return (epoch, item.total_spent)
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        return (last, item.total_spent)

    customers = sorted(buckets.values(), key=sort_key, reverse=True)

    cleaned_query = (q or "").strip().lower()
    if cleaned_query:
        customers = [
            customer
            for customer in customers
            if cleaned_query in customer.customer_name.lower()
            or cleaned_query in (customer.customer_phone or "").lower()
        ]

    resolved_limit, resolved_offset = normalize_page_params(limit, offset)
    return customers[resolved_offset : resolved_offset + resolved_limit]


def list_vendor_products(
    db: Session,
    user: User,
    *,
    q: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[VendorProductRead]:
    require_vendor_access(user)
    resolved_limit, resolved_offset = normalize_page_params(limit, offset)

    statement = select(Product).where(Product.vendor_user_id == user.id)

    cleaned_query = (q or "").strip()
    if cleaned_query:
        pattern = f"%{cleaned_query}%"
        statement = statement.where(
            or_(
                Product.title.ilike(pattern),
                Product.description.ilike(pattern),
                Product.category.ilike(pattern),
            )
        )

    statement = (
        statement.order_by(Product.created_at.desc(), Product.title.asc())
        .offset(resolved_offset)
        .limit(resolved_limit)
    )
    products = list(db.scalars(statement).all())
    from app.services.inventory_service import compute_reserved_stock_map

    reserved_map = compute_reserved_stock_map(db, [product.id for product in products])
    return [
        serialize_vendor_product(
            product,
            reserved_stock=reserved_map.get(product.id, 0),
        )
        for product in products
    ]


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
        stock=0,
        status="pending",
        store_id=store.id,
        vendor_user_id=user.id,
        audience_slug=(store.audience_slugs or [None])[0],
        section=None,
        is_active=False,
    )
    db.add(product)
    db.flush()
    from app.services.inventory_service import record_stock_change

    if int(payload.stock or 0) > 0:
        record_stock_change(
            db,
            product,
            new_stock=int(payload.stock),
            reason="system",
            note="Initial stock on create",
            actor=user,
        )
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
    next_stock = data.pop("stock", None)

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

    if next_stock is not None:
        from app.services.inventory_service import record_stock_change

        record_stock_change(
            db,
            product,
            new_stock=int(next_stock),
            reason="manual",
            note="Updated via product editor",
            actor=user,
        )

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


VENDOR_SELF_SERVICE_PRODUCT_STATUSES = {"active", "hidden"}
VENDOR_RELISTABLE_STATUSES = {"hidden", "out_of_stock"}


def _publish_vendor_product_change(db: Session, user: User, product: Product) -> VendorProductRead:
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


def update_vendor_product_status(
    db: Session,
    user: User,
    product_id: str,
    status: str,
) -> VendorProductRead:
    require_vendor_access(user)
    normalized_status = status.strip().lower()
    if normalized_status not in VENDOR_SELF_SERVICE_PRODUCT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be active or hidden.",
        )

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

    if product.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Suspended products can't be updated from the app.",
        )

    if normalized_status == "hidden":
        if product.status not in {"active", "hidden", "out_of_stock"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only live or out-of-stock products can be hidden.",
            )
        product.status = "hidden"
        product.is_active = False
    else:
        if product.status not in VENDOR_RELISTABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only hidden or out-of-stock products can be republished.",
            )
        if product.stock <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Increase stock before republishing this product.",
            )
        product.status = "active"
        product.is_active = True

    db.commit()
    db.refresh(product)
    return _publish_vendor_product_change(db, user, product)


def patch_vendor_product_stock(
    db: Session,
    user: User,
    product_id: str,
    stock: int,
) -> VendorProductRead:
    from app.services.inventory_service import (
        compute_reserved_stock_map,
        record_stock_change,
    )

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

    if product.status == "suspended":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Suspended products can't be updated from the app.",
        )

    record_stock_change(
        db,
        product,
        new_stock=stock,
        reason="manual",
        note="Stock adjusted from Seller Center",
        actor=user,
    )
    # Restock alone does not republish — vendor must explicitly set status active.
    db.commit()
    db.refresh(product)
    reserved = compute_reserved_stock_map(db, [product.id]).get(product.id, 0)
    result = serialize_vendor_product(product, reserved_stock=reserved)
    # Keep realtime payload aligned with serialize helper used elsewhere.
    broadcast_catalog_product_change(product)
    realtime_manager.publish_user_event_sync(
        str(user.id),
        "vendor.product.updated",
        result.model_dump(mode="json"),
    )
    dashboard = fetch_vendor_dashboard(db, user)
    realtime_manager.publish_user_event_sync(
        str(user.id),
        "vendor.dashboard.updated",
        dashboard.model_dump(mode="json"),
    )
    return result


def bulk_update_vendor_products(
    db: Session,
    user: User,
    *,
    product_ids: list[str],
    stock: int | None = None,
    status: str | None = None,
) -> list[VendorProductRead]:
    from app.services.inventory_service import (
        compute_reserved_stock_map,
        record_stock_change,
    )

    require_vendor_access(user)
    unique_ids = list(dict.fromkeys([pid.strip() for pid in product_ids if pid.strip()]))
    if not unique_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at least one product.",
        )
    if len(unique_ids) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can update at most 50 products at once.",
        )

    products = list(
        db.scalars(
            select(Product).where(
                Product.vendor_user_id == user.id,
                Product.id.in_(unique_ids),
            )
        ).all()
    )
    if len(products) != len(unique_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more products were not found in your catalog.",
        )

    next_status = (status or "").strip().lower() or None
    if next_status and next_status not in {"active", "hidden"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bulk status may only be active or hidden.",
        )

    for product in products:
        if product.status == "suspended":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{product.title} is suspended and can't be bulk-updated.",
            )
        if stock is not None:
            record_stock_change(
                db,
                product,
                new_stock=stock,
                reason="bulk",
                note="Bulk stock update",
                actor=user,
            )
        if next_status == "hidden":
            product.status = "hidden"
            product.is_active = False
        elif next_status == "active":
            if int(product.stock or 0) <= 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Increase stock before republishing {product.title}.",
                )
            product.status = "active"
            product.is_active = True

    db.commit()
    for product in products:
        db.refresh(product)
        broadcast_catalog_product_change(product)

    reserved_map = compute_reserved_stock_map(db, [product.id for product in products])
    results = [
        serialize_vendor_product(product, reserved_stock=reserved_map.get(product.id, 0))
        for product in products
    ]
    for result in results:
        realtime_manager.publish_user_event_sync(
            str(user.id),
            "vendor.product.updated",
            result.model_dump(mode="json"),
        )
    dashboard = fetch_vendor_dashboard(db, user)
    realtime_manager.publish_user_event_sync(
        str(user.id),
        "vendor.dashboard.updated",
        dashboard.model_dump(mode="json"),
    )
    return results


def list_vendor_product_inventory_movements(
    db: Session,
    user: User,
    product_id: str,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> list[VendorInventoryMovementRead]:
    from app.models import InventoryMovement

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

    resolved_limit, resolved_offset = normalize_page_params(limit, offset)
    rows = list(
        db.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.product_id == product_id)
            .order_by(InventoryMovement.created_at.desc())
            .offset(resolved_offset)
            .limit(resolved_limit)
        ).all()
    )
    return [
        VendorInventoryMovementRead(
            id=row.id,
            product_id=row.product_id,
            delta=row.delta,
            stock_after=row.stock_after,
            reason=row.reason,
            reference_type=row.reference_type,
            reference_id=row.reference_id,
            note=row.note,
            created_at=row.created_at,
            created_by_user_id=row.created_by_user_id,
        )
        for row in rows
    ]


def list_vendor_orders(
    db: Session,
    user: User,
    *,
    q: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[VendorOrderRead]:
    require_vendor_access(user)
    orders = list_vendor_orders_payloads(db, user)

    cleaned_query = (q or "").strip().lower()
    if cleaned_query:
        orders = [
            order
            for order in orders
            if cleaned_query in (order.order_number or "").lower()
            or cleaned_query in (order.customer_name or "").lower()
            or cleaned_query in (order.customer_phone or "").lower()
        ]

    resolved_limit, resolved_offset = normalize_page_params(limit, offset)
    return orders[resolved_offset : resolved_offset + resolved_limit]


def list_vendor_return_requests(db: Session, user: User) -> list[VendorReturnRequestRead]:
    require_vendor_access(user)
    requests = list(
        db.scalars(
            select(ReturnRequest)
            .join(OrderItem, ReturnRequest.order_item_id == OrderItem.id)
            .options(
                selectinload(ReturnRequest.order),
                selectinload(ReturnRequest.order_item),
            )
            .where(OrderItem.vendor_user_id == user.id)
            .order_by(ReturnRequest.created_at.desc())
        ).all()
    )
    return [_serialize_vendor_return_request(request) for request in requests]


def get_vendor_return_request(
    db: Session,
    user: User,
    return_request_id: str,
) -> VendorReturnRequestRead:
    require_vendor_access(user)
    request = db.scalar(
        select(ReturnRequest)
        .join(OrderItem, ReturnRequest.order_item_id == OrderItem.id)
        .options(
            selectinload(ReturnRequest.order),
            selectinload(ReturnRequest.order_item),
        )
        .where(
            ReturnRequest.id == return_request_id,
            OrderItem.vendor_user_id == user.id,
        )
    )
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That return request was not found.",
        )
    return _serialize_vendor_return_request(request)


def patch_vendor_return_request(
    db: Session,
    user: User,
    return_request_id: str,
    payload: "VendorReturnRequestUpdate",
) -> VendorReturnRequestRead:
    from app.schemas.vendor import VendorReturnRequestUpdate
    from app.services.return_request_service import update_vendor_return_request

    require_vendor_access(user)
    try:
        request = update_vendor_return_request(
            db,
            user,
            return_request_id,
            status=payload.status.strip().lower(),
            vendor_note=payload.vendor_note,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return _serialize_vendor_return_request(request)


VENDOR_ANALYTICS_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}
VENDOR_ANALYTICS_DEFAULT_PERIOD = "30d"


def _order_timestamp(order: VendorOrderRead) -> datetime:
    return order.placed_at or order.created_at


def _parse_analytics_period(period: str | None) -> tuple[str, int]:
    normalized = (period or VENDOR_ANALYTICS_DEFAULT_PERIOD).strip().lower()
    if normalized not in VENDOR_ANALYTICS_PERIOD_DAYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Period must be one of 7d, 30d, or 90d.",
        )
    return normalized, VENDOR_ANALYTICS_PERIOD_DAYS[normalized]


def _build_vendor_daily_points(
    orders: list[VendorOrderRead],
    *,
    period_start: datetime,
    days: int,
) -> list[VendorAnalyticsDailyPoint]:
    buckets: dict[str, dict[str, float]] = {}
    for offset in range(days):
        day = period_start + timedelta(days=offset)
        buckets[day.date().isoformat()] = {"sales": 0.0, "orders": 0}

    for order in orders:
        if order.status not in VENDOR_ANALYTICS_ORDER_STATUSES:
            continue
        timestamp = _order_timestamp(order)
        if timestamp is None or timestamp < period_start:
            continue
        key = timestamp.date().isoformat()
        bucket = buckets.get(key)
        if bucket is None:
            continue
        bucket["sales"] += float(order.total_amount or 0)
        bucket["orders"] += 1

    return [
        VendorAnalyticsDailyPoint(
            date=day,
            sales=round(values["sales"], 2),
            orders=int(values["orders"]),
        )
        for day, values in sorted(buckets.items())
    ]


def fetch_vendor_analytics(
    db: Session,
    user: User,
    *,
    period: str = VENDOR_ANALYTICS_DEFAULT_PERIOD,
) -> VendorAnalyticsRead:
    require_vendor_access(user)
    normalized_period, period_days = _parse_analytics_period(period)

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)
    period_start = today_start - timedelta(days=period_days - 1)

    orders = list_vendor_orders_payloads(db, user)

    today_orders = [
        order
        for order in orders
        if _order_timestamp(order) >= today_start
        and order.status in VENDOR_ANALYTICS_ORDER_STATUSES
    ]
    week_orders = [
        order
        for order in orders
        if _order_timestamp(order) >= week_start
        and order.status in VENDOR_ANALYTICS_ORDER_STATUSES
    ]
    period_orders = [
        order
        for order in orders
        if _order_timestamp(order) >= period_start
        and order.status in VENDOR_ANALYTICS_ORDER_STATUSES
    ]

    open_returns = (
        db.scalar(
            select(func.count(ReturnRequest.id))
            .join(OrderItem, ReturnRequest.order_item_id == OrderItem.id)
            .where(
                OrderItem.vendor_user_id == user.id,
                ReturnRequest.status.in_(VENDOR_OPEN_RETURN_STATUSES),
            )
        )
        or 0
    )

    top_rows = db.execute(
        select(
            OrderItem.product_id,
            OrderItem.title,
            OrderItem.image_url,
            func.sum(OrderItem.quantity).label("units_sold"),
            func.sum(OrderItem.line_total).label("gross_sales"),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .where(
            OrderItem.vendor_user_id == user.id,
            Order.vendor_status == "delivered",
            func.coalesce(Order.placed_at, Order.created_at) >= period_start,
        )
        .group_by(OrderItem.product_id, OrderItem.title, OrderItem.image_url)
        .order_by(func.sum(OrderItem.line_total).desc())
        .limit(5)
    ).all()

    top_products = [
        VendorTopProductRead(
            product_id=row.product_id,
            product_title=row.title,
            product_image_url=row.image_url,
            units_sold=int(row.units_sold or 0),
            gross_sales=round(float(row.gross_sales or 0), 2),
        )
        for row in top_rows
    ]

    daily_points = _build_vendor_daily_points(
        orders,
        period_start=period_start,
        days=period_days,
    )

    return VendorAnalyticsRead(
        period=normalized_period,
        today_sales=round(sum(order.total_amount for order in today_orders), 2),
        week_sales=round(sum(order.total_amount for order in week_orders), 2),
        today_orders=len(today_orders),
        week_orders=len(week_orders),
        period_sales=round(sum(order.total_amount for order in period_orders), 2),
        period_orders=len(period_orders),
        open_returns=int(open_returns),
        top_products=top_products,
        daily_points=daily_points,
    )


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
        owner_type=getattr(voucher, "owner_type", "vendor") or "vendor",
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
        approval_status=getattr(voucher, "approval_status", "approved"),
        campaign_tag=getattr(voucher, "campaign_tag", None),
        review_notes=getattr(voucher, "review_notes", None),
        product_ids=getattr(voucher, "product_ids", None),
        excluded_product_ids=getattr(voucher, "excluded_product_ids", None),
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
            Voucher.owner_type == "vendor",
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
        .options(selectinload(Order.items), selectinload(Order.user), selectinload(Order.timeline))
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

    other_vendor_ids = {
        item.vendor_user_id
        for item in order.items
        if item.vendor_user_id and item.vendor_user_id != user.id
    }
    is_sole_vendor = len(other_vendor_ids) == 0

    current_status = order.vendor_status
    if next_status != current_status:
        if next_status == "cancelled":
            if current_status not in VENDOR_STATUS_CANCELLABLE_FROM:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"An order that is already {current_status.replace('_', ' ')} "
                        "can no longer be cancelled here."
                    ),
                )
        elif VENDOR_STATUS_FORWARD_TRANSITIONS.get(current_status) != next_status:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Orders move one stage at a time — this order is currently "
                    f"{current_status.replace('_', ' ')}."
                ),
            )

    if next_status == "delivered":
        # Proof of delivery: the vendor must key in the code the customer was
        # shown once the order went out for delivery. No rider/GPS involved —
        # this is the cheapest reliable handoff confirmation for a
        # vendor-fulfilled marketplace.
        entered_code = (payload.delivery_code or "").strip()
        if not order.delivery_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This order hasn't been marked out for delivery yet.",
            )
        if not entered_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter the delivery code the customer gives you to confirm handoff.",
            )
        if entered_code != order.delivery_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That delivery code doesn't match. Ask the customer to double-check it.",
            )

    # Always track this vendor's fulfillment view.
    order.vendor_status = next_status
    record_order_status_event(
        db,
        order,
        status=next_status,
        actor_role="vendor",
        note="Cancelled by store" if next_status == "cancelled" else None,
    )
    changed_wallet_vendor_ids: set[uuid.UUID] = set()
    if next_status == "delivered":
        # Settle this vendor's earnings even on shared carts.
        changed_wallet_vendor_ids = settle_vendor_wallets_for_order(
            db,
            order,
            vendor_scope={user.id},
        )
        if is_sole_vendor:
            order.status = "delivered"
            order.progress = 1
            order.tracking_eta = None
            order.delivered_at = datetime.now(UTC)
            order.cancelled_at = None
            order.cancellation_reason = None
        else:
            # Do not mark the whole customer order delivered when other sellers
            # still have line items on the cart.
            if order.status not in {"delivered", "cancelled", "refunded"}:
                order.status = "processing"
                order.progress = max(float(order.progress or 0), 0.9)
                order.tracking_eta = (
                    order.tracking_eta
                    or "Partially fulfilled · remaining sellers still preparing"
                )
    elif next_status == "cancelled":
        if not is_sole_vendor:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "This order includes items from other sellers. "
                    "Contact ODOS support to cancel a shared cart."
                ),
            )
        order.status = "cancelled"
        order.progress = 0
        order.tracking_eta = None
        order.cancelled_at = datetime.now(UTC)
        order.cancellation_reason = "Cancelled by store"
    elif next_status == "out_for_delivery":
        order.status = "processing"
        order.progress = 0.9
        order.tracking_eta = "Out for delivery · on the way to you"
        order.cancelled_at = None
        order.cancellation_reason = None
        is_new_dispatch = not order.delivery_code
        ensure_delivery_code(order)
        if is_new_dispatch and order.address_phone:
            send_delivery_out_for_delivery_sms(
                phone_number=order.address_phone,
                order_number=order.order_number,
                delivery_code=order.delivery_code,
            )
    else:
        order.status = "processing"
        progress_map = {
            "pending": 0.1,
            "confirmed": 0.2,
            "processing": 0.45,
            "ready": 0.75,
        }
        order.progress = progress_map.get(next_status, order.progress)
        order.tracking_eta = tracking_eta_for_vendor_status(
            next_status,
            order.delivery_method,
            get_delivery_config(db),
        ) or order.tracking_eta
        order.cancelled_at = None
        order.cancellation_reason = None

    push_title, push_body = customer_order_status_push_copy(
        order_number=order.order_number,
        vendor_status=next_status,
        tracking_eta=order.tracking_eta,
    )
    status_event = create_notification_event(
        db,
        order.user,
        kind="vendor_order_update",
        title=push_title,
        body=push_body,
        icon="bag-handle-outline",
        accent="neutral" if next_status != "cancelled" else "warning",
        action_label="Track order",
        route_type="order",
        route_target_id=str(order.id),
        image_key=matching_items[0].image_key if matching_items else None,
    )
    dispatch_customer_order_push(
        user=order.user,
        title=push_title,
        body=push_body,
        order=order,
        notification_event=status_event,
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

    vendor_order = _serialize_vendor_order(db, user, order)
    if not vendor_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found for this vendor.",
        )

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


def _require_owned_vendor_order(db: Session, user: User, order_id: str) -> Order:
    require_vendor_access(user)
    order = db.scalar(
        select(Order)
        .options(
            selectinload(Order.items),
            selectinload(Order.user),
            selectinload(Order.timeline),
        )
        .where(Order.id == order_id)
    )
    if not order or not _matching_vendor_items(db, user, order):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found for this vendor.",
        )
    return order


async def set_vendor_order_dispatch_photo(
    db: Session,
    user: User,
    order_id: str,
    photo: UploadFile,
) -> VendorOrderRead:
    order = _require_owned_vendor_order(db, user, order_id)
    if order.vendor_status in {"delivered", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This order is already {order.vendor_status} — no need for a dispatch photo now.",
        )

    photo_url = await save_image_upload(photo, folder="orders/dispatch")
    order.dispatch_photo_url = photo_url
    db.commit()
    db.refresh(order)

    vendor_order = _serialize_vendor_order(db, user, order)
    if not vendor_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found for this vendor.",
        )
    realtime_manager.publish_user_event_sync(
        str(user.id),
        "vendor.order.updated",
        vendor_order.model_dump(mode="json"),
    )
    return vendor_order


def notify_vendor_order_departure(
    db: Session,
    user: User,
    order_id: str,
) -> VendorOrderRead:
    order = _require_owned_vendor_order(db, user, order_id)

    if order.vendor_status not in {"ready", "out_for_delivery"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can only send this heads-up once the order is ready.",
        )
    if order.departure_notified_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The customer has already been notified you're heading out.",
        )

    order.departure_notified_at = datetime.now(UTC)
    db.commit()
    db.refresh(order)

    preview = order_notification_image(order)
    try:
        departure_event = create_notification_event(
            db,
            order.user,
            kind="vendor_departing",
            title="Your seller is heading out 🚴",
            body=f"Order #{order.order_number} is being brought to you right now!",
            icon="bicycle-outline",
            accent="info",
            action_label="Track order",
            route_type="order",
            route_target_id=str(order.id),
            image_key=preview["image_key"],
            image_url=preview["image_url"],
        )
        dispatch_customer_order_push(
            user=order.user,
            title="Your seller is heading out 🚴",
            body=f"Order #{order.order_number} is being brought to you right now!",
            order=order,
            notification_event=departure_event,
        )
        db.commit()
    except Exception:
        logger.exception(
            "Failed to send departure notice for order %s",
            order.id,
        )

    vendor_order = _serialize_vendor_order(db, user, order)
    if not vendor_order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found for this vendor.",
        )
    return vendor_order


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
                Voucher.owner_type == "vendor",
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
        owner_type="vendor",
        product_ids=payload.product_ids,
    )
    if payload.product_ids:
        owned_count = db.scalar(
            select(func.count(Product.id)).where(
                Product.id.in_(payload.product_ids),
                Product.store_id == store.id,
            )
        )
        if int(owned_count or 0) != len(payload.product_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Product targeting can only include products from your store.",
            )
    discount_value = 0 if payload.discount_type == "free_shipping" else round(payload.discount_value, 2)
    voucher = Voucher(
        code=payload.code,
        title=payload.title,
        description=payload.description,
        issuer_name=payload.issuer_name or store.title,
        scope="store",
        owner_type="vendor",
        availability=payload.availability,
        store_id=store.id,
        reward_text=build_voucher_reward_text(payload.discount_type, discount_value),
        discount_type=payload.discount_type,
        discount_value=discount_value,
        min_subtotal=round(payload.min_subtotal, 2),
        max_discount=round(payload.max_discount, 2) if payload.max_discount is not None else None,
        usage_limit=payload.usage_limit,
        per_user_limit=payload.per_user_limit,
        is_active=False,
        approval_status="pending",
        created_by_user_id=user.id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        product_ids=payload.product_ids,
        excluded_product_ids=payload.excluded_product_ids,
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
    _dispatch_admin_voucher_review_alert(db, voucher=voucher, store_title=store.title)
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
        owner_type="vendor",
        product_ids=payload.product_ids,
    )
    if payload.product_ids:
        owned_count = db.scalar(
            select(func.count(Product.id)).where(
                Product.id.in_(payload.product_ids),
                Product.store_id == store.id,
            )
        )
        if int(owned_count or 0) != len(payload.product_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Product targeting can only include products from your store.",
            )
    voucher = _get_vendor_voucher(db, user, voucher_id)
    discount_value = 0 if payload.discount_type == "free_shipping" else round(payload.discount_value, 2)
    was_approved = getattr(voucher, "approval_status", "approved") == "approved"
    material_fields_changed = (
        voucher.discount_type != payload.discount_type
        or float(voucher.discount_value) != float(discount_value)
        or round(float(voucher.min_subtotal), 2) != round(payload.min_subtotal, 2)
        or (
            (None if voucher.max_discount is None else round(float(voucher.max_discount), 2))
            != (None if payload.max_discount is None else round(payload.max_discount, 2))
        )
        or voucher.usage_limit != payload.usage_limit
        or voucher.per_user_limit != payload.per_user_limit
        or voucher.starts_at != payload.starts_at
        or voucher.ends_at != payload.ends_at
        or list(getattr(voucher, "product_ids", None) or []) != list(payload.product_ids or [])
        or list(getattr(voucher, "excluded_product_ids", None) or [])
        != list(payload.excluded_product_ids or [])
    )

    voucher.code = payload.code
    voucher.title = payload.title
    voucher.description = payload.description
    voucher.issuer_name = payload.issuer_name or store.title
    voucher.owner_type = "vendor"
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
    voucher.product_ids = payload.product_ids
    voucher.excluded_product_ids = payload.excluded_product_ids

    # Material economic/eligibility changes require admin re-approval.
    if was_approved and material_fields_changed:
        voucher.approval_status = "pending"
        voucher.is_active = False
        voucher.review_notes = "Updated by vendor — awaiting re-approval."
        voucher.reviewed_by_user_id = None

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


def list_vendor_voucher_redemptions(
    db: Session,
    user: User,
    voucher_id: str,
    *,
    limit: int = 50,
) -> list[VendorVoucherRedemptionRead]:
    require_vendor_access(user)
    voucher = _get_vendor_voucher(db, user, voucher_id)
    rows = list(
        db.scalars(
            select(VoucherRedemption)
            .where(VoucherRedemption.voucher_id == voucher.id)
            .order_by(VoucherRedemption.created_at.desc())
            .limit(max(1, min(limit, 200)))
        ).all()
    )
    return [
        VendorVoucherRedemptionRead(
            id=row.id,
            order_id=row.order_id,
            voucher_code=row.voucher_code,
            discount_amount=round(float(row.discount_amount), 2),
            user_id=row.user_id,
            created_at=row.created_at,
        )
        for row in rows
    ]


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
    is_on_vacation: bool | None = None,
    vacation_message: str | None = None,
    business_hours: dict | None = None,
    business_hours_provided: bool = False,
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
    if is_on_vacation is not None:
        store.is_on_vacation = is_on_vacation
    store.vacation_message = vacation_message.strip() if vacation_message else None
    if business_hours_provided:
        store.business_hours = business_hours

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


def list_vendor_applications(
    db: Session,
    user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[VendorApplicationListItem]:
    require_admin(user)
    statement = (
        select(VendorApplication)
        .options(selectinload(VendorApplication.user))
        .order_by(
            VendorApplication.submitted_at.desc(),
            VendorApplication.created_at.desc(),
        )
    )
    applications, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[
            VendorApplicationListItem(
                **VendorApplicationRead.model_validate(application).model_dump(),
                full_name=application.user.full_name,
                email=application.user.email,
            )
            for application in applications
        ],
        has_more=has_more,
    )


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
