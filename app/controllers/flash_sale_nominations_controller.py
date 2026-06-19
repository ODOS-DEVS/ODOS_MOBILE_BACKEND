"""Vendor flash sale nominations and admin review."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.admin_pagination import paginate_scalars
from app.schemas.pagination import AdminPageRead
from app.controllers.admin_controller import broadcast_catalog_flash_sale_event_change
from app.controllers.vendor_controller import get_vendor_store, require_vendor_access
from app.models import FlashSaleEvent, FlashSaleEventProduct, FlashSaleNomination, Product, User
from app.schemas.admin import AdminFlashSaleNominationRead, AdminFlashSaleNominationReview
from app.schemas.vendor import VendorFlashSaleNominationCreate, VendorFlashSaleNominationRead


def _serialize_vendor_nomination(nomination: FlashSaleNomination) -> VendorFlashSaleNominationRead:
    return VendorFlashSaleNominationRead(
        id=nomination.id,
        event_id=nomination.event_id,
        product_id=nomination.product_id,
        proposed_price=nomination.proposed_price,
        proposed_old_price=nomination.proposed_old_price,
        stock_limit=nomination.stock_limit,
        max_per_user=nomination.max_per_user,
        vendor_note=nomination.vendor_note,
        status=nomination.status,
        review_notes=nomination.review_notes,
        created_at=nomination.created_at,
        updated_at=nomination.updated_at,
    )


def _serialize_admin_nomination(
    db: Session,
    nomination: FlashSaleNomination,
) -> AdminFlashSaleNominationRead:
    product = db.get(Product, nomination.product_id)
    event = db.get(FlashSaleEvent, nomination.event_id) if nomination.event_id else None
    return AdminFlashSaleNominationRead(
        id=nomination.id,
        event_id=nomination.event_id,
        event_title=event.title if event else None,
        product_id=nomination.product_id,
        product_title=product.title if product else None,
        vendor_user_id=nomination.vendor_user_id,
        proposed_price=nomination.proposed_price,
        proposed_old_price=nomination.proposed_old_price,
        stock_limit=nomination.stock_limit,
        max_per_user=nomination.max_per_user,
        vendor_note=nomination.vendor_note,
        status=nomination.status,
        review_notes=nomination.review_notes,
        created_at=nomination.created_at,
        updated_at=nomination.updated_at,
    )


def list_vendor_flash_sale_nominations(db: Session, user: User) -> list[VendorFlashSaleNominationRead]:
    require_vendor_access(user)
    nominations = list(
        db.scalars(
            select(FlashSaleNomination)
            .where(FlashSaleNomination.vendor_user_id == user.id)
            .order_by(FlashSaleNomination.created_at.desc())
        ).all()
    )
    return [_serialize_vendor_nomination(item) for item in nominations]


def create_vendor_flash_sale_nomination(
    db: Session,
    user: User,
    payload: VendorFlashSaleNominationCreate,
) -> VendorFlashSaleNominationRead:
    require_vendor_access(user)
    store = get_vendor_store(db, user)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No managed store was found for this vendor.",
        )

    product = db.scalar(
        select(Product).where(
            Product.id == payload.product_id,
            Product.store_id == store.id,
            Product.is_active.is_(True),
        )
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found in your store.",
        )

    event = None
    if payload.event_id:
        event = db.get(FlashSaleEvent, payload.event_id)
        if not event:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Flash sale event not found.",
            )

    if payload.proposed_price is not None and payload.proposed_price >= product.price:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Flash sale price must be lower than the current product price.",
        )

    nomination = FlashSaleNomination(
        event_id=payload.event_id,
        product_id=payload.product_id,
        vendor_user_id=user.id,
        proposed_price=payload.proposed_price,
        proposed_old_price=payload.proposed_old_price or product.price,
        stock_limit=payload.stock_limit,
        max_per_user=payload.max_per_user,
        vendor_note=payload.vendor_note,
        status="pending",
    )
    db.add(nomination)
    db.commit()
    db.refresh(nomination)
    return _serialize_vendor_nomination(nomination)


def list_admin_flash_sale_nominations(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
) -> AdminPageRead[AdminFlashSaleNominationRead]:
    from app.controllers.admin_controller import require_admin

    require_admin(current_user)
    statement = select(FlashSaleNomination).order_by(FlashSaleNomination.created_at.desc())
    nominations, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=[_serialize_admin_nomination(db, item) for item in nominations],
        has_more=has_more,
    )


def review_admin_flash_sale_nomination(
    db: Session,
    current_user: User,
    nomination_id: str,
    payload: AdminFlashSaleNominationReview,
) -> AdminFlashSaleNominationRead:
    from app.controllers.admin_controller import require_admin

    require_admin(current_user)

    try:
        normalized_id = uuid.UUID(str(nomination_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nomination not found.",
        ) from exc

    nomination = db.get(FlashSaleNomination, normalized_id)
    if not nomination:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nomination not found.")

    if payload.status not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be approved or rejected.",
        )

    nomination.status = payload.status
    nomination.reviewed_by_user_id = current_user.id
    nomination.review_notes = payload.review_notes

    if payload.status == "approved":
        event = None
        if nomination.event_id:
            event = db.get(FlashSaleEvent, nomination.event_id)
        elif payload.event_id:
            event = db.get(FlashSaleEvent, payload.event_id)
            nomination.event_id = payload.event_id

        if not event:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A flash sale event is required to approve this nomination.",
            )

        flash_price = payload.flash_sale_price or nomination.proposed_price
        flash_old_price = payload.flash_sale_old_price or nomination.proposed_old_price
        if flash_price is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Flash sale price is required for approval.",
            )

        existing = db.scalar(
            select(FlashSaleEventProduct).where(
                FlashSaleEventProduct.event_id == event.id,
                FlashSaleEventProduct.product_id == nomination.product_id,
            )
        )
        if existing:
            existing.flash_sale_price = flash_price
            existing.flash_sale_old_price = flash_old_price
        else:
            max_sort = db.scalar(
                select(FlashSaleEventProduct.sort_order)
                .where(FlashSaleEventProduct.event_id == event.id)
                .order_by(FlashSaleEventProduct.sort_order.desc())
            )
            db.add(
                FlashSaleEventProduct(
                    event_id=event.id,
                    product_id=nomination.product_id,
                    sort_order=int(max_sort or 0) + 1,
                    flash_sale_price=flash_price,
                    flash_sale_old_price=flash_old_price,
                )
            )

        broadcast_catalog_flash_sale_event_change(event)

    db.commit()
    db.refresh(nomination)
    return _serialize_admin_nomination(db, nomination)
