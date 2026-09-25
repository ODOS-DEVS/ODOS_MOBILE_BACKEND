"""Admin promo banners and flash sale events: CRUD, archiving, and the
catalogue broadcasts that keep clients in sync.
"""

import uuid

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.admin_pagination import paginate_scalars
from app.core.auth import require_admin
from app.core.promo_banner_config import (
    PROMO_CAMPAIGN_TAGS,
    describe_promo_destination,
    normalize_promo_link_type,
    normalize_promo_placement,
)
from app.models import (
    FlashSaleEvent,
    FlashSaleEventProduct,
    Product,
    PromoBanner,
    User,
)
from app.schemas.admin import (
    AdminFlashSaleEventRead,
    AdminFlashSaleEventUpsert,
    AdminPromoBannerRead,
    AdminPromoBannerUpsert,
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


def _validate_promo_banner_payload(payload: AdminPromoBannerUpsert) -> None:
    link_type = normalize_promo_link_type(payload.link_type)
    placement = normalize_promo_placement(payload.placement)
    target = (payload.cta_link or "").strip()
    campaign_tag = (payload.campaign_tag or "").strip()

    if link_type in {"category", "product", "store", "external", "screen"} and not target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose a destination target for this banner tap action.",
        )
    if link_type == "campaign" and not campaign_tag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Choose a campaign for this banner.",
        )
    if link_type == "discounted_products" and target:
        try:
            percent = int(target)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Minimum discount must be a whole number between 1 and 90.",
            ) from exc
        if percent < 1 or percent > 90:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Minimum discount must be between 1 and 90 percent.",
            )
    if link_type == "external" and target and not (
        target.startswith("http://") or target.startswith("https://")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="External links must start with http:// or https://",
        )
    if campaign_tag and campaign_tag not in dict(PROMO_CAMPAIGN_TAGS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported campaign tag.",
        )
    if placement not in {"home", "deals"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported banner placement.",
        )


def _serialize_promo_banner(banner: PromoBanner) -> AdminPromoBannerRead:
    link_type = normalize_promo_link_type(banner.link_type)
    placement = normalize_promo_placement(banner.placement)
    return AdminPromoBannerRead(
        id=banner.id,
        title=banner.title,
        subtitle=banner.subtitle,
        cta_label=banner.cta_label,
        cta_link=banner.cta_link,
        image_url=banner.image_url,
        accent=banner.accent,
        sort_order=banner.sort_order,
        status="active" if banner.is_active else "disabled",
        link_type=link_type,
        campaign_tag=banner.campaign_tag,
        placement=placement,
        destination_label=describe_promo_destination(
            link_type=link_type,
            cta_link=banner.cta_link,
            campaign_tag=banner.campaign_tag,
        ),
        starts_at=banner.starts_at,
        ends_at=banner.ends_at,
        created_at=banner.created_at,
        updated_at=banner.updated_at,
    )


def broadcast_catalog_promo_banner_change(banner: PromoBanner) -> None:
    from app.core.cache import invalidate_catalog_promo_banners

    invalidate_catalog_promo_banners()
    realtime_manager.broadcast_event_sync(
        "catalog.promo_banner.changed",
        {
            "banner_id": str(banner.id),
            "status": "active" if banner.is_active else "disabled",
            "is_active": banner.is_active,
        },
    )


def list_admin_promo_banners(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminPromoBannerRead]:
    require_admin(current_user)
    statement = select(PromoBanner).order_by(
        PromoBanner.sort_order.asc(),
        PromoBanner.created_at.desc(),
    )
    banners, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_promo_banner(banner) for banner in banners],
        has_more=has_more,
    )


async def create_admin_promo_banner(
    db: Session,
    current_user: User,
    payload: AdminPromoBannerUpsert,
    image_file: UploadFile | None = None,
) -> AdminPromoBannerRead:
    require_admin(current_user)
    _validate_promo_banner_payload(payload)
    if payload.starts_at and payload.ends_at and payload.ends_at < payload.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after the start date.",
        )

    next_sort_order = payload.sort_order
    if next_sort_order is None:
        next_sort_order = (db.scalar(select(func.coalesce(func.max(PromoBanner.sort_order), 0))) or 0) + 1

    banner = PromoBanner(
        title=payload.title,
        subtitle=payload.subtitle,
        cta_label=payload.cta_label or "Shop now",
        cta_link=payload.cta_link,
        accent=payload.accent,
        sort_order=next_sort_order,
        is_active=payload.status != "disabled",
        link_type=normalize_promo_link_type(payload.link_type),
        campaign_tag=payload.campaign_tag,
        placement=normalize_promo_placement(payload.placement),
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )
    if image_file:
        banner.image_url = await save_image_upload(image_file, folder="promo-banners")

    db.add(banner)
    db.commit()
    db.refresh(banner)
    broadcast_catalog_promo_banner_change(banner)
    return _serialize_promo_banner(banner)


async def update_admin_promo_banner(
    db: Session,
    current_user: User,
    banner_id: str,
    payload: AdminPromoBannerUpsert,
    image_file: UploadFile | None = None,
) -> AdminPromoBannerRead:
    require_admin(current_user)
    _validate_promo_banner_payload(payload)
    if payload.starts_at and payload.ends_at and payload.ends_at < payload.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after the start date.",
        )

    try:
        normalized_id = uuid.UUID(str(banner_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.") from exc

    banner = db.scalar(select(PromoBanner).where(PromoBanner.id == normalized_id))
    if not banner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.")

    banner.title = payload.title
    banner.subtitle = payload.subtitle
    banner.cta_label = payload.cta_label or "Shop now"
    banner.cta_link = payload.cta_link
    banner.accent = payload.accent
    banner.link_type = normalize_promo_link_type(payload.link_type)
    banner.campaign_tag = payload.campaign_tag
    banner.placement = normalize_promo_placement(payload.placement)
    if payload.sort_order is not None:
        banner.sort_order = payload.sort_order
    banner.is_active = payload.status != "disabled"
    banner.starts_at = payload.starts_at
    banner.ends_at = payload.ends_at

    if image_file:
        if banner.image_url:
            remove_media_file(banner.image_url)
        banner.image_url = await save_image_upload(image_file, folder="promo-banners")

    db.commit()
    db.refresh(banner)
    broadcast_catalog_promo_banner_change(banner)
    return _serialize_promo_banner(banner)


def get_admin_promo_banner(db: Session, current_user: User, banner_id: str) -> AdminPromoBannerRead:
    require_admin(current_user)
    try:
        normalized_id = uuid.UUID(str(banner_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.") from exc

    banner = db.scalar(select(PromoBanner).where(PromoBanner.id == normalized_id))
    if not banner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.")

    return _serialize_promo_banner(banner)


def archive_admin_promo_banner(db: Session, current_user: User, banner_id: str) -> None:
    require_admin(current_user)
    try:
        normalized_id = uuid.UUID(str(banner_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.") from exc

    banner = db.scalar(select(PromoBanner).where(PromoBanner.id == normalized_id))
    if not banner:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Promo banner not found.")

    banner.is_active = False
    db.commit()
    broadcast_catalog_promo_banner_change(banner)


def _normalize_flash_event_slug(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value.lower().strip())
    return "-".join(segment for segment in cleaned.split("-") if segment)


def _serialize_flash_sale_event(
    db: Session,
    event: FlashSaleEvent,
) -> AdminFlashSaleEventRead:
    product_ids = list(
        db.scalars(
            select(FlashSaleEventProduct.product_id)
            .where(FlashSaleEventProduct.event_id == event.id)
            .order_by(FlashSaleEventProduct.sort_order.asc(), FlashSaleEventProduct.product_id.asc())
        ).all()
    )
    return AdminFlashSaleEventRead(
        id=event.id,
        slug=event.slug,
        title=event.title,
        subtitle=event.subtitle,
        image_url=event.image_url,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        sort_order=event.sort_order,
        status="active" if event.is_active else "disabled",
        product_ids=product_ids,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def broadcast_catalog_flash_sale_event_change(event: FlashSaleEvent) -> None:
    from app.core.cache import invalidate_catalog_flash_sale_events

    invalidate_catalog_flash_sale_events()
    realtime_manager.broadcast_event_sync(
        "catalog.flash_sale_event.changed",
        {
            "event_id": str(event.id),
            "slug": event.slug,
            "status": "active" if event.is_active else "disabled",
            "is_active": event.is_active,
        },
    )


def _replace_flash_sale_event_products(
    db: Session,
    event: FlashSaleEvent,
    product_ids: list[str],
) -> None:
    normalized_ids: list[str] = []
    seen: set[str] = set()
    for product_id in product_ids:
        cleaned = product_id.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized_ids.append(cleaned)

    if normalized_ids:
        existing_count = db.scalar(
            select(func.count())
            .select_from(Product)
            .where(
                Product.id.in_(normalized_ids),
                Product.is_active.is_(True),
            )
        )
        if existing_count != len(normalized_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more selected products could not be found.",
            )

    db.execute(
        FlashSaleEventProduct.__table__.delete().where(
            FlashSaleEventProduct.event_id == event.id
        )
    )
    for index, product_id in enumerate(normalized_ids):
        db.add(
            FlashSaleEventProduct(
                event_id=event.id,
                product_id=product_id,
                sort_order=index + 1,
            )
        )


def list_admin_flash_sale_events(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminFlashSaleEventRead]:
    require_admin(current_user)
    statement = select(FlashSaleEvent).order_by(
        FlashSaleEvent.sort_order.asc(),
        FlashSaleEvent.ends_at.desc(),
    )
    events, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_flash_sale_event(db, event) for event in events],
        has_more=has_more,
    )


def create_admin_flash_sale_event(
    db: Session,
    current_user: User,
    payload: AdminFlashSaleEventUpsert,
) -> AdminFlashSaleEventRead:
    require_admin(current_user)
    slug = _normalize_flash_event_slug(payload.slug)
    if not slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Event slug is required.")

    if payload.starts_at and payload.ends_at < payload.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after the start date.",
        )

    existing = db.scalar(select(FlashSaleEvent).where(FlashSaleEvent.slug == slug))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A flash sale event with this slug already exists.",
        )

    next_sort_order = payload.sort_order
    if next_sort_order is None:
        next_sort_order = (db.scalar(select(func.coalesce(func.max(FlashSaleEvent.sort_order), 0))) or 0) + 1

    event = FlashSaleEvent(
        slug=slug,
        title=payload.title,
        subtitle=payload.subtitle,
        sort_order=next_sort_order,
        is_active=payload.status != "disabled",
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )
    db.add(event)
    db.flush()
    _replace_flash_sale_event_products(db, event, payload.product_ids)
    db.commit()
    db.refresh(event)
    broadcast_catalog_flash_sale_event_change(event)
    return _serialize_flash_sale_event(db, event)


def update_admin_flash_sale_event(
    db: Session,
    current_user: User,
    event_id: str,
    payload: AdminFlashSaleEventUpsert,
) -> AdminFlashSaleEventRead:
    require_admin(current_user)
    slug = _normalize_flash_event_slug(payload.slug)
    if not slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Event slug is required.")

    if payload.starts_at and payload.ends_at < payload.starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="End date must be after the start date.",
        )

    try:
        normalized_id = uuid.UUID(str(event_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flash sale event not found.") from exc

    event = db.scalar(select(FlashSaleEvent).where(FlashSaleEvent.id == normalized_id))
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flash sale event not found.")

    conflict = db.scalar(
        select(FlashSaleEvent).where(
            FlashSaleEvent.slug == slug,
            FlashSaleEvent.id != normalized_id,
        )
    )
    if conflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A flash sale event with this slug already exists.",
        )

    event.slug = slug
    event.title = payload.title
    event.subtitle = payload.subtitle
    if payload.sort_order is not None:
        event.sort_order = payload.sort_order
    event.is_active = payload.status != "disabled"
    event.starts_at = payload.starts_at
    event.ends_at = payload.ends_at
    _replace_flash_sale_event_products(db, event, payload.product_ids)
    db.commit()
    db.refresh(event)
    broadcast_catalog_flash_sale_event_change(event)
    return _serialize_flash_sale_event(db, event)


def archive_admin_flash_sale_event(db: Session, current_user: User, event_id: str) -> None:
    require_admin(current_user)
    try:
        normalized_id = uuid.UUID(str(event_id))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flash sale event not found.") from exc

    event = db.scalar(select(FlashSaleEvent).where(FlashSaleEvent.id == normalized_id))
    if not event:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flash sale event not found.")

    event.is_active = False
    db.commit()
    broadcast_catalog_flash_sale_event_change(event)
