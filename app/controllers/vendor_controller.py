import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.notification_controller import create_notification_event
from app.models import (
    Market,
    Order,
    Product,
    Store,
    User,
    UserRole,
    VendorApplication,
    VendorStatus,
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
)
from app.services.media_service import remove_media_file, save_image_upload, save_image_uploads

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
        status=product.status,
        created_at=product.created_at,
        updated_at=product.updated_at,
    )


def list_vendor_orders_payloads(db: Session, user: User) -> list[VendorOrderRead]:
    vendor_products = list(
        db.scalars(select(Product).where(Product.vendor_user_id == user.id)).all()
    )
    vendor_product_map = {product.id: product for product in vendor_products}
    if not vendor_product_map:
        return []

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
            item for item in order.items if item.product_id in vendor_product_map
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
    product = Product(
        id=generate_product_id(),
        title=payload.name,
        description=payload.description,
        category=payload.category,
        subcategory=payload.subcategory,
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
        stock=payload.stock,
        status="active",
        store_id=store.id,
        vendor_user_id=user.id,
        audience_slug=(store.audience_slugs or [None])[0],
        section=None,
    )
    db.add(product)
    db.commit()
    db.refresh(product)

    return serialize_vendor_product(product)


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

    db.commit()
    db.refresh(product)
    return serialize_vendor_product(product)


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

    db.delete(product)
    db.commit()


def list_vendor_orders(db: Session, user: User) -> list[VendorOrderRead]:
    require_vendor_access(user)
    return list_vendor_orders_payloads(db, user)


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
    if next_status == "delivered":
        order.status = "delivered"
        order.progress = 1
        order.tracking_eta = None
        order.delivered_at = datetime.now(UTC)
        order.cancelled_at = None
        order.cancellation_reason = None
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

    vendor_orders = list_vendor_orders_payloads(db, user)
    for vendor_order in vendor_orders:
        if str(vendor_order.id) == order_id:
            return vendor_order

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="That order was not found for this vendor.",
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
