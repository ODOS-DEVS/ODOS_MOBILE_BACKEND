"""Admin + customer controllers for merchandising campaigns."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.controllers.catalog_controller import serialize_catalog_products
from app.controllers.notification_controller import create_notification_event
from app.core.admin_permissions import list_admins_with_feature
from app.core.cache import cache_delete_matching
from app.core.config import settings
from app.models import Order, OrderItem, Product, User
from app.models.catalog import (
    MerchandisingCampaign,
    MerchandisingCampaignOptIn,
    MerchandisingCampaignProduct,
)
from app.services.email_service import send_admin_campaign_opt_in_email
from app.services.push_service import build_push_data, send_expo_push_notification
from app.services.sms_service import notify_admins_by_sms

logger = logging.getLogger(__name__)


def _dispatch_admin_campaign_opt_in_alert(
    db: Session,
    *,
    vendor: User,
    campaign_title: str,
    product_title: str,
) -> None:
    admins = list_admins_with_feature(db, "promotions")
    for admin in admins:
        if not admin.email:
            continue
        try:
            send_admin_campaign_opt_in_email(
                to_email=admin.email,
                to_name=admin.full_name,
                vendor_name=vendor.full_name or vendor.email,
                campaign_title=campaign_title,
                product_title=product_title,
                submitted_at_label=datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC"),
                admin_panel_url=settings.admin_panel_url,
            )
        except Exception:
            logger.exception(
                "Failed to send admin campaign-opt-in alert to %s",
                admin.email,
            )

    notify_admins_by_sms(
        db,
        feature="promotions",
        message=(
            f"ODOS: {vendor.full_name or vendor.email} submitted {product_title} for the "
            f"'{campaign_title}' campaign. Review in the admin panel."
        ),
    )
from app.schemas.admin import (
    AdminMerchandisingCampaignOptInRead,
    AdminMerchandisingCampaignRead,
    AdminMerchandisingCampaignUpsert,
)
from app.schemas.pagination import AdminPageRead
from app.schemas.catalog import MerchandisingCampaignDetailRead, MerchandisingCampaignRead
from app.services.campaign_service import (
    CAMPAIGN_STATUSES,
    PRODUCT_SORT_MODES,
    VISIBILITY_MODES,
    campaign_is_live,
    count_campaign_products,
    derive_campaign_status,
    get_campaign_by_slug,
    get_campaign_target_maps,
    list_live_campaigns,
    replace_campaign_targets,
    resolve_campaign_products,
    slugify_campaign,
    sync_campaign_schedule_status,
)
from app.services.media_service import save_image_upload


def invalidate_merchandising_campaigns() -> None:
    cache_delete_matching("catalog:campaigns*")
    cache_delete_matching("catalog:deals-hub*")


def _seconds_remaining(ends_at: datetime | None) -> int:
    if not ends_at:
        return 0
    now = datetime.now(timezone.utc)
    end = ends_at if ends_at.tzinfo else ends_at.replace(tzinfo=timezone.utc)
    return max(0, int((end - now).total_seconds()))


def _serialize_public_campaign(
    db: Session,
    campaign: MerchandisingCampaign,
    *,
    include_product_count: bool = True,
) -> MerchandisingCampaignRead:
    sync_campaign_schedule_status(db, campaign)
    return MerchandisingCampaignRead(
        id=campaign.id,
        slug=campaign.slug,
        title=campaign.title,
        subtitle=campaign.subtitle,
        description=campaign.description,
        banner_image_url=campaign.banner_image_url,
        thumbnail_image_url=campaign.thumbnail_image_url,
        icon_key=campaign.icon_key,
        theme_color=campaign.theme_color,
        is_featured=campaign.is_featured,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        display_priority=campaign.display_priority,
        product_count=count_campaign_products(db, campaign) if include_product_count else 0,
        seconds_remaining=_seconds_remaining(campaign.ends_at),
    )


def _serialize_admin_campaign(db: Session, campaign: MerchandisingCampaign) -> AdminMerchandisingCampaignRead:
    manual_rows, categories, stores, _ = get_campaign_target_maps(db, campaign.id)
    sync_campaign_schedule_status(db, campaign)
    return AdminMerchandisingCampaignRead(
        id=campaign.id,
        slug=campaign.slug,
        title=campaign.title,
        subtitle=campaign.subtitle,
        description=campaign.description,
        banner_image_url=campaign.banner_image_url,
        thumbnail_image_url=campaign.thumbnail_image_url,
        icon_key=campaign.icon_key,
        theme_color=campaign.theme_color,
        status=derive_campaign_status(campaign),
        is_active=campaign.is_active,
        is_featured=campaign.is_featured,
        visibility=campaign.visibility,
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        display_priority=campaign.display_priority,
        max_products=campaign.max_products,
        product_sort=campaign.product_sort,
        hide_out_of_stock=campaign.hide_out_of_stock,
        include_entire_marketplace=campaign.include_entire_marketplace,
        allow_vendor_opt_in=campaign.allow_vendor_opt_in,
        product_ids=[row.product_id for row in manual_rows],
        pinned_product_ids=[row.product_id for row in manual_rows if row.is_pinned],
        category_slugs=categories,
        store_ids=stores,
        product_count=count_campaign_products(db, campaign),
        created_at=campaign.created_at,
        updated_at=campaign.updated_at,
    )


def _validate_upsert(payload: AdminMerchandisingCampaignUpsert) -> None:
    status_value = (payload.status or "draft").strip().lower()
    if status_value not in CAMPAIGN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported campaign status '{payload.status}'.",
        )
    if payload.product_sort not in PRODUCT_SORT_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported product_sort '{payload.product_sort}'.",
        )
    if payload.visibility not in VISIBILITY_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported visibility '{payload.visibility}'.",
        )
    if payload.starts_at and payload.ends_at and payload.starts_at >= payload.ends_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Campaign end time must be after start time.",
        )


def list_public_campaigns(
    db: Session,
    *,
    featured_only: bool = False,
    limit: int = 40,
    user_id: str | None = None,
) -> list[MerchandisingCampaignRead]:
    campaigns = list_live_campaigns(db, featured_only=featured_only, limit=limit, user_id=user_id)
    return [_serialize_public_campaign(db, campaign) for campaign in campaigns]


def get_public_campaign_detail(
    db: Session,
    slug: str,
    *,
    limit: int = 30,
    offset: int = 0,
) -> MerchandisingCampaignDetailRead:
    campaign = get_campaign_by_slug(db, slug)
    if not campaign or not campaign_is_live(campaign):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That campaign is not available.",
        )

    products, total = resolve_campaign_products(db, campaign, limit=limit, offset=offset)
    serialized = serialize_catalog_products(db, products)
    base = _serialize_public_campaign(db, campaign, include_product_count=False)
    return MerchandisingCampaignDetailRead(
        **base.model_dump(),
        product_count=total,
        products=serialized,
        has_more=offset + len(serialized) < total,
    )


def list_admin_campaigns(
    db: Session,
    current_user: User,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    status_filter: str | None = None,
) -> AdminPageRead[AdminMerchandisingCampaignRead]:
    filters = []
    if search and search.strip():
        term = f"%{search.strip().lower()}%"
        filters.append(
            or_(
                MerchandisingCampaign.title.ilike(term),
                MerchandisingCampaign.slug.ilike(term),
                MerchandisingCampaign.subtitle.ilike(term),
            )
        )
    if status_filter and status_filter != "all":
        filters.append(MerchandisingCampaign.status == status_filter.strip().lower())

    query = select(MerchandisingCampaign)
    if filters:
        query = query.where(*filters)
    query = query.order_by(
        MerchandisingCampaign.display_priority.asc(),
        MerchandisingCampaign.updated_at.desc(),
    )

    rows = list(db.scalars(query.offset(offset).limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    return AdminPageRead(
        items=[_serialize_admin_campaign(db, row) for row in rows],
        has_more=has_more,
    )


async def create_admin_campaign(
    db: Session,
    current_user: User,
    payload: AdminMerchandisingCampaignUpsert,
    *,
    banner_file: UploadFile | None = None,
    thumbnail_file: UploadFile | None = None,
) -> AdminMerchandisingCampaignRead:
    _validate_upsert(payload)
    slug = slugify_campaign(payload.slug or payload.title)
    existing = get_campaign_by_slug(db, slug)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A campaign with that slug already exists.",
        )

    campaign = MerchandisingCampaign(
        id=uuid.uuid4(),
        slug=slug,
        title=payload.title.strip(),
        subtitle=payload.subtitle,
        description=payload.description,
        banner_image_url=payload.banner_image_url,
        thumbnail_image_url=payload.thumbnail_image_url,
        icon_key=payload.icon_key,
        theme_color=payload.theme_color,
        status=(payload.status or "draft").strip().lower(),
        is_active=bool(payload.is_active),
        is_featured=bool(payload.is_featured),
        visibility=(payload.visibility or "public").strip().lower(),
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        display_priority=payload.display_priority or 0,
        max_products=payload.max_products,
        product_sort=payload.product_sort or "manual",
        hide_out_of_stock=bool(payload.hide_out_of_stock),
        include_entire_marketplace=bool(payload.include_entire_marketplace),
        allow_vendor_opt_in=bool(payload.allow_vendor_opt_in),
    )
    if banner_file is not None and banner_file.filename:
        campaign.banner_image_url = await save_image_upload(
            banner_file, folder="merchandising-campaigns"
        )
    if thumbnail_file is not None and thumbnail_file.filename:
        campaign.thumbnail_image_url = await save_image_upload(
            thumbnail_file, folder="merchandising-campaigns"
        )

    db.add(campaign)
    db.flush()
    replace_campaign_targets(
        db,
        campaign,
        product_ids=payload.product_ids,
        pinned_product_ids=payload.pinned_product_ids,
        category_slugs=payload.category_slugs,
        store_ids=payload.store_ids,
    )
    sync_campaign_schedule_status(db, campaign)
    db.commit()
    db.refresh(campaign)
    invalidate_merchandising_campaigns()
    return _serialize_admin_campaign(db, campaign)


async def update_admin_campaign(
    db: Session,
    current_user: User,
    campaign_id: uuid.UUID,
    payload: AdminMerchandisingCampaignUpsert,
    *,
    banner_file: UploadFile | None = None,
    thumbnail_file: UploadFile | None = None,
) -> AdminMerchandisingCampaignRead:
    _validate_upsert(payload)
    campaign = db.get(MerchandisingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    new_slug = slugify_campaign(payload.slug or payload.title)
    if new_slug != campaign.slug:
        conflict = get_campaign_by_slug(db, new_slug)
        if conflict and conflict.id != campaign.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A campaign with that slug already exists.",
            )
        campaign.slug = new_slug

    campaign.title = payload.title.strip()
    campaign.subtitle = payload.subtitle
    campaign.description = payload.description
    if payload.banner_image_url is not None:
        campaign.banner_image_url = payload.banner_image_url
    if payload.thumbnail_image_url is not None:
        campaign.thumbnail_image_url = payload.thumbnail_image_url
    campaign.icon_key = payload.icon_key
    campaign.theme_color = payload.theme_color
    campaign.status = (payload.status or campaign.status).strip().lower()
    campaign.is_active = bool(payload.is_active)
    campaign.is_featured = bool(payload.is_featured)
    campaign.visibility = (payload.visibility or "public").strip().lower()
    campaign.starts_at = payload.starts_at
    campaign.ends_at = payload.ends_at
    campaign.display_priority = payload.display_priority or 0
    campaign.max_products = payload.max_products
    campaign.product_sort = payload.product_sort or "manual"
    campaign.hide_out_of_stock = bool(payload.hide_out_of_stock)
    campaign.include_entire_marketplace = bool(payload.include_entire_marketplace)
    campaign.allow_vendor_opt_in = bool(payload.allow_vendor_opt_in)

    if banner_file is not None and banner_file.filename:
        campaign.banner_image_url = await save_image_upload(
            banner_file, folder="merchandising-campaigns"
        )
    if thumbnail_file is not None and thumbnail_file.filename:
        campaign.thumbnail_image_url = await save_image_upload(
            thumbnail_file, folder="merchandising-campaigns"
        )

    replace_campaign_targets(
        db,
        campaign,
        product_ids=payload.product_ids,
        pinned_product_ids=payload.pinned_product_ids,
        category_slugs=payload.category_slugs,
        store_ids=payload.store_ids,
    )
    sync_campaign_schedule_status(db, campaign)
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    invalidate_merchandising_campaigns()
    return _serialize_admin_campaign(db, campaign)


def get_admin_campaign(
    db: Session,
    current_user: User,
    campaign_id: uuid.UUID,
) -> AdminMerchandisingCampaignRead:
    campaign = db.get(MerchandisingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
    return _serialize_admin_campaign(db, campaign)


def archive_admin_campaign(
    db: Session,
    current_user: User,
    campaign_id: uuid.UUID,
) -> None:
    campaign = db.get(MerchandisingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
    campaign.status = "archived"
    campaign.is_active = False
    db.add(campaign)
    db.commit()
    invalidate_merchandising_campaigns()


def duplicate_admin_campaign(
    db: Session,
    current_user: User,
    campaign_id: uuid.UUID,
) -> AdminMerchandisingCampaignRead:
    source = db.get(MerchandisingCampaign, campaign_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    base_slug = f"{source.slug}-copy"
    slug = base_slug
    suffix = 2
    while get_campaign_by_slug(db, slug):
        slug = f"{base_slug}-{suffix}"
        suffix += 1

    clone = MerchandisingCampaign(
        id=uuid.uuid4(),
        slug=slug,
        title=f"{source.title} (Copy)",
        subtitle=source.subtitle,
        description=source.description,
        banner_image_url=source.banner_image_url,
        thumbnail_image_url=source.thumbnail_image_url,
        icon_key=source.icon_key,
        theme_color=source.theme_color,
        status="draft",
        is_active=False,
        is_featured=False,
        visibility=source.visibility,
        starts_at=source.starts_at,
        ends_at=source.ends_at,
        display_priority=source.display_priority,
        max_products=source.max_products,
        product_sort=source.product_sort,
        hide_out_of_stock=source.hide_out_of_stock,
        include_entire_marketplace=source.include_entire_marketplace,
        allow_vendor_opt_in=source.allow_vendor_opt_in,
    )
    db.add(clone)
    db.flush()

    manual_rows, categories, stores, _ = get_campaign_target_maps(db, source.id)
    replace_campaign_targets(
        db,
        clone,
        product_ids=[row.product_id for row in manual_rows],
        pinned_product_ids=[row.product_id for row in manual_rows if row.is_pinned],
        category_slugs=categories,
        store_ids=stores,
    )
    db.commit()
    db.refresh(clone)
    invalidate_merchandising_campaigns()
    return _serialize_admin_campaign(db, clone)


def _units_sold_since_approval(db: Session, opt_in: MerchandisingCampaignOptIn) -> int | None:
    if opt_in.status != "approved":
        return None
    total = db.scalar(
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            OrderItem.product_id == opt_in.product_id,
            Order.payment_status == "paid",
            Order.created_at >= opt_in.updated_at,
        )
    )
    return int(total or 0)


def list_admin_campaign_opt_ins(
    db: Session,
    current_user: User,
    *,
    campaign_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> AdminPageRead[AdminMerchandisingCampaignOptInRead]:
    query = select(MerchandisingCampaignOptIn).order_by(
        MerchandisingCampaignOptIn.created_at.desc()
    )
    if campaign_id:
        query = query.where(MerchandisingCampaignOptIn.campaign_id == campaign_id)
    if status_filter and status_filter != "all":
        query = query.where(MerchandisingCampaignOptIn.status == status_filter.strip().lower())

    rows = list(db.scalars(query.offset(offset).limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]

    items: list[AdminMerchandisingCampaignOptInRead] = []
    for row in rows:
        product = db.get(Product, row.product_id)
        campaign = db.get(MerchandisingCampaign, row.campaign_id)
        vendor = db.get(User, row.vendor_user_id)
        items.append(
            AdminMerchandisingCampaignOptInRead(
                id=row.id,
                campaign_id=row.campaign_id,
                campaign_slug=campaign.slug if campaign else "",
                campaign_title=campaign.title if campaign else "",
                product_id=row.product_id,
                product_title=product.title if product else row.product_id,
                vendor_user_id=row.vendor_user_id,
                vendor_name=(vendor.full_name or vendor.email) if vendor else None,
                status=row.status,
                review_notes=row.review_notes,
                units_sold_since_approval=_units_sold_since_approval(db, row),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )

    return AdminPageRead(items=items, has_more=has_more)


def review_admin_campaign_opt_in(
    db: Session,
    current_user: User,
    opt_in_id: uuid.UUID,
    *,
    status_value: str,
    review_notes: str | None = None,
) -> AdminMerchandisingCampaignOptInRead:
    row = db.get(MerchandisingCampaignOptIn, opt_in_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Opt-in not found.")

    cleaned = status_value.strip().lower()
    if cleaned not in {"approved", "rejected", "pending"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Status must be approved, rejected, or pending.",
        )

    row.status = cleaned
    row.review_notes = review_notes
    row.reviewed_by_user_id = current_user.id
    db.add(row)

    if cleaned == "approved":
        exists = db.scalar(
            select(MerchandisingCampaignProduct).where(
                MerchandisingCampaignProduct.campaign_id == row.campaign_id,
                MerchandisingCampaignProduct.product_id == row.product_id,
            )
        )
        if not exists:
            db.add(
                MerchandisingCampaignProduct(
                    campaign_id=row.campaign_id,
                    product_id=row.product_id,
                    sort_order=999,
                    is_pinned=False,
                )
            )

    db.commit()
    db.refresh(row)
    invalidate_merchandising_campaigns()

    product = db.get(Product, row.product_id)
    campaign = db.get(MerchandisingCampaign, row.campaign_id)
    vendor = db.get(User, row.vendor_user_id)

    if cleaned in {"approved", "rejected"}:
        if vendor:
            product_title = product.title if product else "your product"
            campaign_title = campaign.title if campaign else "the campaign"
            if cleaned == "approved":
                title = "Campaign opt-in approved"
                body = f"{product_title} is now featured in {campaign_title}."
            else:
                title = "Campaign opt-in declined"
                body = f"{product_title} was not selected for {campaign_title}."
                if review_notes:
                    body = f"{body} {review_notes}"

            notification_event = create_notification_event(
                db,
                vendor,
                kind=f"vendor_campaign_opt_in_{cleaned}",
                title=title,
                body=body,
                icon="megaphone-outline" if cleaned == "approved" else "close-circle-outline",
                accent="success" if cleaned == "approved" else "neutral",
                action_label="View campaigns",
                route_type="vendor_campaign",
                route_target_id=str(row.id),
            )
            if vendor.expo_push_token and vendor.allow_notifications:
                try:
                    send_expo_push_notification(
                        user=vendor,
                        title=title,
                        body=body,
                        data=build_push_data(
                            push_type="vendor_campaign_opt_in",
                            route_type="vendor_campaign",
                            route_target_id=str(row.id),
                            notification_event=notification_event,
                            extra={"optInId": str(row.id)},
                        ),
                    )
                except Exception:
                    logger.exception(
                        "Failed to push campaign opt-in review alert to vendor %s",
                        vendor.id,
                    )
            db.commit()

    return AdminMerchandisingCampaignOptInRead(
        id=row.id,
        campaign_id=row.campaign_id,
        campaign_slug=campaign.slug if campaign else "",
        campaign_title=campaign.title if campaign else "",
        product_id=row.product_id,
        product_title=product.title if product else row.product_id,
        vendor_user_id=row.vendor_user_id,
        vendor_name=(vendor.full_name or vendor.email) if vendor else None,
        status=row.status,
        review_notes=row.review_notes,
        units_sold_since_approval=_units_sold_since_approval(db, row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_vendor_campaign_opt_ins(
    db: Session,
    vendor_user: User,
) -> list[AdminMerchandisingCampaignOptInRead]:
    from app.controllers.vendor_controller import require_vendor_access

    require_vendor_access(vendor_user)
    rows = list(
        db.scalars(
            select(MerchandisingCampaignOptIn)
            .where(MerchandisingCampaignOptIn.vendor_user_id == vendor_user.id)
            .order_by(MerchandisingCampaignOptIn.created_at.desc())
        ).all()
    )
    if not rows:
        return []

    campaign_ids = {row.campaign_id for row in rows}
    product_ids = {row.product_id for row in rows}
    campaigns = {
        campaign.id: campaign
        for campaign in db.scalars(
            select(MerchandisingCampaign).where(MerchandisingCampaign.id.in_(campaign_ids))
        ).all()
    }
    products = {
        product.id: product
        for product in db.scalars(select(Product).where(Product.id.in_(product_ids))).all()
    }

    return [
        AdminMerchandisingCampaignOptInRead(
            id=row.id,
            campaign_id=row.campaign_id,
            campaign_slug=(campaigns.get(row.campaign_id).slug if campaigns.get(row.campaign_id) else ""),
            campaign_title=(
                campaigns.get(row.campaign_id).title if campaigns.get(row.campaign_id) else "Campaign"
            ),
            product_id=row.product_id,
            product_title=(
                products.get(row.product_id).title if products.get(row.product_id) else row.product_id
            ),
            vendor_user_id=row.vendor_user_id,
            status=row.status,
            review_notes=row.review_notes,
            units_sold_since_approval=_units_sold_since_approval(db, row),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def list_vendor_open_campaigns(db: Session, vendor_user: User) -> list[MerchandisingCampaignRead]:
    from app.controllers.vendor_controller import require_vendor_access

    require_vendor_access(vendor_user)
    campaigns = [
        campaign
        for campaign in list_live_campaigns(db, limit=60)
        if campaign.allow_vendor_opt_in
    ]
    return [_serialize_public_campaign(db, campaign) for campaign in campaigns]


def create_vendor_campaign_opt_in(
    db: Session,
    vendor_user: User,
    *,
    campaign_id: uuid.UUID,
    product_id: str,
) -> AdminMerchandisingCampaignOptInRead:
    from app.controllers.vendor_controller import require_vendor_access

    require_vendor_access(vendor_user)
    campaign = db.get(MerchandisingCampaign, campaign_id)
    if not campaign or not campaign.allow_vendor_opt_in or not campaign_is_live(campaign):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That campaign is not open for vendor participation.",
        )

    product = db.get(Product, product_id)
    if not product or product.vendor_user_id != vendor_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only nominate your own products.",
        )

    existing = db.scalar(
        select(MerchandisingCampaignOptIn).where(
            MerchandisingCampaignOptIn.campaign_id == campaign_id,
            MerchandisingCampaignOptIn.product_id == product_id,
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This product is already submitted for the campaign.",
        )

    row = MerchandisingCampaignOptIn(
        id=uuid.uuid4(),
        campaign_id=campaign_id,
        product_id=product_id,
        vendor_user_id=vendor_user.id,
        status="pending",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    _dispatch_admin_campaign_opt_in_alert(
        db,
        vendor=vendor_user,
        campaign_title=campaign.title,
        product_title=product.title,
    )

    return AdminMerchandisingCampaignOptInRead(
        id=row.id,
        campaign_id=row.campaign_id,
        campaign_slug=campaign.slug,
        campaign_title=campaign.title,
        product_id=row.product_id,
        product_title=product.title,
        vendor_user_id=row.vendor_user_id,
        status=row.status,
        review_notes=row.review_notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
