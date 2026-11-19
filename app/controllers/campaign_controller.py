"""Admin + customer controllers for merchandising campaigns."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

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
from app.models.user import VendorStatus
from app.schemas.admin import (
    AdminMerchandisingCampaignOptInRead,
    AdminMerchandisingCampaignRead,
    AdminMerchandisingCampaignUpsert,
)
from app.schemas.catalog import (
    CampaignParticipationRead,
    MerchandisingCampaignDetailRead,
    MerchandisingCampaignRead,
    VendorCampaignBriefRead,
    VendorCampaignSubmissionItem,
    VendorCampaignSubmissionOutcome,
    VendorCampaignSubmissionRead,
    VendorCampaignSubmissionResult,
    VendorCampaignWorkspaceRead,
)
from app.schemas.pagination import AdminPageRead
from app.services.campaign_service import (
    CAMPAIGN_STATUSES,
    PRODUCT_SORT_MODES,
    VISIBILITY_MODES,
    campaign_accepts_submissions,
    campaign_is_live,
    campaign_is_published,
    count_campaign_products,
    derive_campaign_status,
    get_campaign_by_slug,
    get_campaign_target_maps,
    list_live_campaigns,
    replace_campaign_targets,
    resolve_campaign_products,
    slugify_campaign,
    submission_window_closes_at,
    sync_campaign_schedule_status,
)
from app.services.email_service import send_admin_campaign_opt_in_email
from app.services.media_service import save_image_upload
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
                submitted_at_label=datetime.now(UTC).strftime("%d %b %Y, %I:%M %p UTC"),
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


def invalidate_merchandising_campaigns() -> None:
    cache_delete_matching("catalog:campaigns*")
    cache_delete_matching("catalog:deals-hub*")


def _seconds_remaining(ends_at: datetime | None) -> int:
    if not ends_at:
        return 0
    now = datetime.now(UTC)
    end = ends_at if ends_at.tzinfo else ends_at.replace(tzinfo=UTC)
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
        submission_deadline=campaign.submission_deadline,
        requires_proposed_price=campaign.requires_proposed_price,
        min_discount_percent=campaign.min_discount_percent,
        vendors_notified_at=campaign.vendors_notified_at,
        vendors_notified_count=campaign.vendors_notified_count or 0,
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


def _apply_campaign_pricing(
    db: Session,
    campaign: MerchandisingCampaign,
    products: list,
) -> list:
    """Overlay the approved campaign price onto the products this campaign returns.

    The campaign price lives on the approved nomination, never on `products.price`: a
    product in a Christmas sale is still its normal price everywhere else on ODOS, and
    the moment the campaign ends it costs what it always did with nothing to undo.

    Only approved nominations count, and only ones that actually undercut the shelf price
    — a stale nomination from before a price drop must not invent a higher "sale" price.
    """
    if not products:
        return products

    priced = dict(
        db.execute(
            select(
                MerchandisingCampaignOptIn.product_id,
                MerchandisingCampaignOptIn.proposed_price,
            ).where(
                MerchandisingCampaignOptIn.campaign_id == campaign.id,
                MerchandisingCampaignOptIn.status == "approved",
                MerchandisingCampaignOptIn.proposed_price.is_not(None),
            )
        ).all()
    )
    if not priced:
        return products

    for product in products:
        campaign_price = priced.get(product.id)
        if campaign_price is None or campaign_price >= product.price:
            continue
        # Strike through what the shopper would pay today, not any older RRP the product
        # carries. The saving a campaign advertises has to be the saving it actually gives,
        # and it has to match the percentage the admin approved.
        was = product.price
        product.old_price = was
        product.price = campaign_price
        product.discount = f"-{int(round((1 - campaign_price / was) * 100))}%"

    return products


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
    serialized = _apply_campaign_pricing(db, campaign, serialize_catalog_products(db, products))
    base = _serialize_public_campaign(db, campaign, include_product_count=False)
    return MerchandisingCampaignDetailRead(
        # `product_count` is excluded rather than relying on include_product_count=False: that
        # flag only skips *computing* the count, while the field keeps its default and still
        # comes out of model_dump() — so passing the real total alongside it raised
        # "got multiple values for keyword argument 'product_count'" and every campaign landing
        # page returned a 500.
        **base.model_dump(exclude={"product_count"}),
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
        submission_deadline=payload.submission_deadline,
        requires_proposed_price=bool(payload.requires_proposed_price),
        min_discount_percent=payload.min_discount_percent,
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
    # Invites go out on publication, never on a saved draft: a vendor asked to prepare
    # stock for a campaign that is not real yet has been misled.
    if payload.notify_vendors:
        notify_vendors_of_campaign(db, campaign)
        db.refresh(campaign)
    return _serialize_admin_campaign(db, campaign)


async def set_admin_campaign_artwork(
    db: Session,
    _current_user: User,
    campaign_id: uuid.UUID,
    *,
    banner_image: UploadFile | None = None,
    thumbnail_image: UploadFile | None = None,
) -> AdminMerchandisingCampaignRead:
    """Replace a campaign's artwork without resubmitting the rest of it.

    `create_admin_campaign` and `update_admin_campaign` already accept these files, but their
    routes take a JSON body and FastAPI cannot mix a JSON body with file parts — so artwork had
    no way in from the dashboard, and an admin had to paste a URL for an image they had on disk.

    Only the files that are actually sent are written, so uploading a new banner leaves an
    existing thumbnail alone.
    """
    # Authorisation is the route's RequirePromotionsAdmin dependency, as with every other admin
    # campaign call in this module — none of them re-check here.
    campaign = db.get(MerchandisingCampaign, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That campaign was not found.",
        )

    if banner_image is not None and banner_image.filename:
        campaign.banner_image_url = await save_image_upload(
            banner_image, folder="merchandising-campaigns"
        )
    if thumbnail_image is not None and thumbnail_image.filename:
        campaign.thumbnail_image_url = await save_image_upload(
            thumbnail_image, folder="merchandising-campaigns"
        )

    db.commit()
    db.refresh(campaign)
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
    campaign.submission_deadline = payload.submission_deadline
    campaign.requires_proposed_price = bool(payload.requires_proposed_price)
    campaign.min_discount_percent = payload.min_discount_percent

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
    # A draft saved with `notify_vendors` set is not an invitation: the fan-out checks
    # publication itself and no-ops, so the flag can stay on through the whole edit cycle
    # and fires exactly once, on the save that actually publishes.
    if payload.notify_vendors:
        notify_vendors_of_campaign(db, campaign)
        db.refresh(campaign)
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
                proposed_price=row.proposed_price,
                proposed_old_price=row.proposed_old_price,
                current_price=product.price if product else None,
                discount_percent=_discount_percent(
                    row.proposed_old_price or (product.price if product else None),
                    row.proposed_price,
                ),
                vendor_note=row.vendor_note,
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
    campaign_price: int | None = None,
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
    if campaign_price is not None:
        # An admin may settle on a different campaign price than the vendor proposed. It is
        # still only the campaign price — `products.price` is never touched here.
        product_now = db.get(Product, row.product_id)
        if product_now and (campaign_price <= 0 or campaign_price >= product_now.price):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"The campaign price must be below the current price of {product_now.price}.",
            )
        row.proposed_price = campaign_price
        if product_now and row.proposed_old_price is None:
            row.proposed_old_price = product_now.price
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

    if cleaned in {"approved", "rejected"} and vendor:
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
        proposed_price=row.proposed_price,
        proposed_old_price=row.proposed_old_price,
        current_price=product.price if product else None,
        discount_percent=_discount_percent(
            row.proposed_old_price or (product.price if product else None),
            row.proposed_price,
        ),
        vendor_note=row.vendor_note,
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
            proposed_price=row.proposed_price,
            proposed_old_price=row.proposed_old_price,
            current_price=(
                products.get(row.product_id).price if products.get(row.product_id) else None
            ),
            discount_percent=_discount_percent(
                row.proposed_old_price
                or (products.get(row.product_id).price if products.get(row.product_id) else None),
                row.proposed_price,
            ),
            vendor_note=row.vendor_note,
            status=row.status,
            review_notes=row.review_notes,
            units_sold_since_approval=_units_sold_since_approval(db, row),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def list_vendor_open_campaigns(db: Session, vendor_user: User) -> list[VendorCampaignBriefRead]:
    """Campaigns a vendor can nominate into right now.

    Not the same set as the shopper-facing live list, and deliberately so: nominations for a
    Christmas sale are collected in November, while `list_live_campaigns` would not show that
    campaign until December. Querying published campaigns directly is what lets a vendor
    prepare before the sale opens.
    """
    from app.controllers.vendor_controller import require_vendor_access

    require_vendor_access(vendor_user)
    rows = list(
        db.scalars(
            select(MerchandisingCampaign)
            .where(
                MerchandisingCampaign.allow_vendor_opt_in.is_(True),
                MerchandisingCampaign.is_active.is_(True),
                MerchandisingCampaign.status.notin_(["draft", "archived", "ended"]),
            )
            .order_by(
                MerchandisingCampaign.is_featured.desc(),
                MerchandisingCampaign.display_priority.asc(),
                MerchandisingCampaign.updated_at.desc(),
            )
            .limit(60)
        ).all()
    )
    return [
        _serialize_campaign_brief(db, campaign)
        for campaign in rows
        if campaign_accepts_submissions(campaign)
    ]


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


# ---------------------------------------------------------------------------
# Vendor invitations
# ---------------------------------------------------------------------------

#: Vendors are notified in chunks so a marketplace with thousands of approved
#: vendors does not build one enormous transaction, and a failure part-way
#: through leaves the vendors already notified actually notified.
VENDOR_NOTIFY_CHUNK = 200


def _format_deadline(moment: datetime | None) -> str | None:
    if not moment:
        return None
    stamped = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    return stamped.strftime("%-d %B %Y")


def eligible_vendor_ids(db: Session) -> list[uuid.UUID]:
    """Approved vendors only.

    Suspended, rejected, pending and withdrawn vendors are excluded by the same
    `vendor_status == approved` test `require_vendor_access` enforces on the way in, so
    nobody is ever invited to a workflow the API would then refuse them.
    """
    return list(
        db.scalars(
            select(User.id).where(User.vendor_status == VendorStatus.APPROVED)
        ).all()
    )


def notify_vendors_of_campaign(
    db: Session,
    campaign: MerchandisingCampaign,
    *,
    force: bool = False,
) -> int:
    """Invite every eligible vendor to submit products. Returns how many were notified.

    Idempotent by `vendors_notified_at`: the second call for the same campaign is a no-op,
    so republishing, a double-click in the admin, or a retried request cannot produce a
    second invitation. `force` re-sends deliberately (a deadline extension, say) and is
    only reachable from the explicit admin action.
    """
    if not campaign_is_published(campaign) or not campaign.allow_vendor_opt_in:
        return 0
    if campaign.vendors_notified_at and not force:
        return 0

    vendor_ids = eligible_vendor_ids(db)
    if not vendor_ids:
        campaign.vendors_notified_at = datetime.now(UTC)
        campaign.vendors_notified_count = 0
        db.add(campaign)
        db.commit()
        return 0

    deadline = _format_deadline(submission_window_closes_at(campaign))
    title = f"New campaign: {campaign.title}"
    body = (
        f"ODOS is preparing {campaign.title} and your store is invited. "
        "Pick the products you want featured and submit them for review."
    )
    if deadline:
        body = f"{body} Submissions close {deadline}."

    # Claim the fan-out before sending. Committing the stamp first means a crash mid-send
    # cannot be replayed into a second round of notifications for everyone.
    campaign.vendors_notified_at = datetime.now(UTC)
    db.add(campaign)
    db.commit()

    notified = 0
    for start in range(0, len(vendor_ids), VENDOR_NOTIFY_CHUNK):
        chunk = vendor_ids[start : start + VENDOR_NOTIFY_CHUNK]
        vendors = list(db.scalars(select(User).where(User.id.in_(chunk))).all())
        for vendor in vendors:
            try:
                event = create_notification_event(
                    db,
                    vendor,
                    kind="vendor_campaign_invite",
                    title=title,
                    body=body,
                    icon="megaphone-outline",
                    accent="promo",
                    action_label="Submit products",
                    # The slug, not the id: it is what both clients route on, and it is
                    # what the submission page is addressed by.
                    route_type="vendor_campaign_submit",
                    route_target_id=campaign.slug,
                    image_url=campaign.thumbnail_image_url or campaign.banner_image_url,
                )
                notified += 1
            except Exception:
                logger.exception(
                    "Failed to create campaign invite for vendor %s on campaign %s",
                    vendor.id,
                    campaign.slug,
                )
                continue

            if vendor.expo_push_token and vendor.allow_notifications:
                try:
                    send_expo_push_notification(
                        user=vendor,
                        title=title,
                        body=body,
                        data=build_push_data(
                            push_type="vendor_campaign_invite",
                            route_type="vendor_campaign_submit",
                            route_target_id=campaign.slug,
                            notification_event=event,
                            extra={"campaignSlug": campaign.slug},
                        ),
                    )
                except Exception:
                    # A dead push token must not cost the vendor their in-app notification,
                    # which is already committed below.
                    logger.exception("Failed to push campaign invite to vendor %s", vendor.id)
        db.commit()

    campaign.vendors_notified_count = notified
    db.add(campaign)
    db.commit()
    logger.info("Invited %s vendors to campaign %s", notified, campaign.slug)
    return notified


# ---------------------------------------------------------------------------
# Vendor submissions
# ---------------------------------------------------------------------------


def _vendor_campaign_or_404(db: Session, slug: str) -> MerchandisingCampaign:
    campaign = get_campaign_by_slug(db, slug)
    if not campaign or not campaign_is_published(campaign) or not campaign.allow_vendor_opt_in:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That campaign is not open for vendor participation.",
        )
    return campaign


def _discount_percent(old_price: int | None, new_price: int | None) -> int | None:
    if not old_price or new_price is None or old_price <= 0:
        return None
    return int(round((1 - (new_price / old_price)) * 100))


def _serialize_campaign_brief(
    db: Session,
    campaign: MerchandisingCampaign,
) -> VendorCampaignBriefRead:
    closes = submission_window_closes_at(campaign)
    return VendorCampaignBriefRead(
        id=campaign.id,
        slug=campaign.slug,
        title=campaign.title,
        subtitle=campaign.subtitle,
        description=campaign.description,
        banner_image_url=campaign.banner_image_url,
        thumbnail_image_url=campaign.thumbnail_image_url,
        theme_color=campaign.theme_color,
        status=derive_campaign_status(campaign),
        starts_at=campaign.starts_at,
        ends_at=campaign.ends_at,
        submission_deadline=campaign.submission_deadline,
        submission_closes_at=closes,
        accepts_submissions=campaign_accepts_submissions(campaign),
        requires_proposed_price=campaign.requires_proposed_price,
        min_discount_percent=campaign.min_discount_percent,
        max_products=campaign.max_products,
        product_count=count_campaign_products(db, campaign),
    )


def _serialize_vendor_submission(
    row: MerchandisingCampaignOptIn,
    product: Product | None,
) -> VendorCampaignSubmissionRead:
    return VendorCampaignSubmissionRead(
        id=row.id,
        campaign_id=row.campaign_id,
        product_id=row.product_id,
        product_title=product.title if product else row.product_id,
        product_image_url=(product.image_url if product else None),
        current_price=product.price if product else None,
        proposed_price=row.proposed_price,
        proposed_old_price=row.proposed_old_price,
        discount_percent=_discount_percent(row.proposed_old_price, row.proposed_price),
        vendor_note=row.vendor_note,
        status=row.status,
        review_notes=row.review_notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def get_vendor_campaign_brief(
    db: Session,
    vendor_user: User,
    slug: str,
) -> VendorCampaignWorkspaceRead:
    """Everything the submission page needs: the campaign, its rules, and this vendor's entries."""
    from app.controllers.vendor_controller import require_vendor_access

    require_vendor_access(vendor_user)
    campaign = _vendor_campaign_or_404(db, slug)

    rows = list(
        db.scalars(
            select(MerchandisingCampaignOptIn)
            .where(
                MerchandisingCampaignOptIn.campaign_id == campaign.id,
                # Scoped to the caller, not to a vendor id supplied by the browser.
                MerchandisingCampaignOptIn.vendor_user_id == vendor_user.id,
            )
            .order_by(MerchandisingCampaignOptIn.created_at.desc())
        ).all()
    )
    products = {
        product.id: product
        for product in db.scalars(
            select(Product).where(Product.id.in_([row.product_id for row in rows]))
        ).all()
    } if rows else {}

    return VendorCampaignWorkspaceRead(
        campaign=_serialize_campaign_brief(db, campaign),
        submissions=[_serialize_vendor_submission(row, products.get(row.product_id)) for row in rows],
    )


def submit_vendor_campaign_products(
    db: Session,
    vendor_user: User,
    slug: str,
    items: list[VendorCampaignSubmissionItem],
) -> VendorCampaignSubmissionResult:
    """Nominate several products in one request.

    Each item is accepted or refused on its own. One ineligible product in a batch of ten
    must not discard the other nine — the vendor would have no way to tell which of their
    selections actually landed.
    """
    from app.controllers.vendor_controller import require_vendor_access

    require_vendor_access(vendor_user)
    campaign = _vendor_campaign_or_404(db, slug)

    if not campaign_accepts_submissions(campaign):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Submissions for this campaign have closed.",
        )
    if not items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Choose at least one product to submit.",
        )

    # One product cannot be submitted twice inside a single batch either.
    seen: set[str] = set()
    outcomes: list[VendorCampaignSubmissionOutcome] = []
    accepted = 0

    existing_ids = set(
        db.scalars(
            select(MerchandisingCampaignOptIn.product_id).where(
                MerchandisingCampaignOptIn.campaign_id == campaign.id,
                MerchandisingCampaignOptIn.vendor_user_id == vendor_user.id,
            )
        ).all()
    )

    for item in items:
        product_id = (item.product_id or "").strip()
        if not product_id or product_id in seen:
            outcomes.append(
                VendorCampaignSubmissionOutcome(
                    product_id=product_id,
                    accepted=False,
                    reason="duplicate",
                    detail="That product appears twice in this submission.",
                )
            )
            continue
        seen.add(product_id)

        product = db.get(Product, product_id)
        # Ownership is checked against the authenticated vendor, never against anything
        # the browser sent. A product belonging to another store is reported as not found
        # rather than as forbidden, so this cannot be used to probe the catalogue.
        if not product or product.vendor_user_id != vendor_user.id:
            outcomes.append(
                VendorCampaignSubmissionOutcome(
                    product_id=product_id,
                    accepted=False,
                    reason="not_yours",
                    detail="We couldn't find that product in your store.",
                )
            )
            continue

        if product_id in existing_ids:
            outcomes.append(
                VendorCampaignSubmissionOutcome(
                    product_id=product_id,
                    accepted=False,
                    reason="already_submitted",
                    detail="You have already submitted this product to this campaign.",
                )
            )
            continue

        proposed = item.proposed_price
        if campaign.requires_proposed_price and proposed is None:
            outcomes.append(
                VendorCampaignSubmissionOutcome(
                    product_id=product_id,
                    accepted=False,
                    reason="price_required",
                    detail="This campaign requires a campaign price for every product.",
                )
            )
            continue

        if proposed is not None:
            if proposed <= 0 or proposed >= product.price:
                outcomes.append(
                    VendorCampaignSubmissionOutcome(
                        product_id=product_id,
                        accepted=False,
                        reason="price_invalid",
                        detail=f"The campaign price must be below the current price of {product.price}.",
                    )
                )
                continue
            floor = campaign.min_discount_percent
            if floor:
                offered = _discount_percent(product.price, proposed) or 0
                if offered < floor:
                    outcomes.append(
                        VendorCampaignSubmissionOutcome(
                            product_id=product_id,
                            accepted=False,
                            reason="discount_too_small",
                            detail=f"This campaign needs at least {floor}% off; that price is {offered}%.",
                        )
                    )
                    continue

        db.add(
            MerchandisingCampaignOptIn(
                id=uuid.uuid4(),
                campaign_id=campaign.id,
                product_id=product_id,
                vendor_user_id=vendor_user.id,
                status="pending",
                proposed_price=proposed,
                # Snapshot of the shelf price. The product's own price is left untouched:
                # nothing about a nomination may change what shoppers pay today.
                proposed_old_price=product.price if proposed is not None else None,
                vendor_note=(item.vendor_note or "").strip() or None,
            )
        )
        existing_ids.add(product_id)
        accepted += 1
        outcomes.append(
            VendorCampaignSubmissionOutcome(product_id=product_id, accepted=True, reason=None, detail=None)
        )

    if accepted:
        db.commit()
        _dispatch_admin_campaign_submission_alert(
            db,
            vendor=vendor_user,
            campaign_title=campaign.title,
            product_count=accepted,
        )
    else:
        db.rollback()

    return VendorCampaignSubmissionResult(
        campaign_slug=campaign.slug,
        submitted=accepted,
        rejected=len(outcomes) - accepted,
        outcomes=outcomes,
    )


def _dispatch_admin_campaign_submission_alert(
    db: Session,
    *,
    vendor: User,
    campaign_title: str,
    product_count: int,
) -> None:
    """One alert per batch, not one per product — a vendor submitting twenty products
    should not bury the admin's notification list."""
    vendor_name = vendor.full_name or vendor.email or "A vendor"
    title = "New campaign submissions"
    body = (
        f"{vendor_name} submitted {product_count} "
        f"{'product' if product_count == 1 else 'products'} to {campaign_title}."
    )
    try:
        for admin in list_admins_with_feature(db, "promotions"):
            create_notification_event(
                db,
                admin,
                kind="admin_campaign_opt_in",
                title=title,
                body=body,
                icon="megaphone-outline",
                accent="promo",
                action_label="Review submissions",
                route_type="admin_campaigns",
            )
        db.commit()
    except Exception:
        # The vendor's submission is already committed; failing to tell an admin must not
        # undo it. The nominations are still visible in the admin review queue.
        logger.exception("Failed to alert admins about campaign submissions")
        db.rollback()


def get_campaign_participation(
    db: Session,
    campaign_id: uuid.UUID,
) -> CampaignParticipationRead:
    """Who was invited, who turned up, and where their products got to."""
    campaign = db.get(MerchandisingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")

    rows = list(
        db.execute(
            select(
                MerchandisingCampaignOptIn.status,
                func.count(MerchandisingCampaignOptIn.id),
            )
            .where(MerchandisingCampaignOptIn.campaign_id == campaign_id)
            .group_by(MerchandisingCampaignOptIn.status)
        ).all()
    )
    by_status = {str(name): int(count) for name, count in rows}

    vendors_submitted = int(
        db.scalar(
            select(func.count(func.distinct(MerchandisingCampaignOptIn.vendor_user_id))).where(
                MerchandisingCampaignOptIn.campaign_id == campaign_id
            )
        )
        or 0
    )
    eligible = int(
        db.scalar(select(func.count(User.id)).where(User.vendor_status == VendorStatus.APPROVED))
        or 0
    )

    return CampaignParticipationRead(
        campaign_id=campaign.id,
        campaign_slug=campaign.slug,
        vendors_notified=campaign.vendors_notified_count or 0,
        vendors_notified_at=campaign.vendors_notified_at,
        vendors_submitted=vendors_submitted,
        eligible_vendors=eligible,
        products_nominated=sum(by_status.values()),
        products_approved=by_status.get("approved", 0),
        products_rejected=by_status.get("rejected", 0),
        products_pending=by_status.get("pending", 0),
    )


def notify_campaign_vendors_action(
    db: Session,
    campaign_id: uuid.UUID,
    *,
    force: bool = False,
) -> CampaignParticipationRead:
    """Admin-triggered invite. Refuses rather than silently doing nothing when the
    campaign is not in a state where vendors could act on the invitation."""
    campaign = db.get(MerchandisingCampaign, campaign_id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campaign not found.")
    if not campaign_is_published(campaign):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Publish the campaign before inviting vendors.",
        )
    if not campaign.allow_vendor_opt_in:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Turn on vendor participation before inviting vendors.",
        )
    if campaign.vendors_notified_at and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Vendors have already been invited to this campaign.",
        )

    notify_vendors_of_campaign(db, campaign, force=force)
    return get_campaign_participation(db, campaign_id)
