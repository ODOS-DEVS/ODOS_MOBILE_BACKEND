"""Vendor participation in merchandising: campaign opt-ins, flash sale
nominations, vouchers and promotions.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.controllers.vendor._shared import get_vendor_store, require_vendor_access
from app.controllers.voucher_controller import (
    assign_voucher_to_user,
    build_voucher_reward_text,
    validate_voucher_configuration,
    voucher_status,
)
from app.core.admin_permissions import list_admins_with_feature
from app.core.config import settings
from app.models import (
    Product,
    User,
    Voucher,
    VoucherRedemption,
)
from app.schemas.vendor import (
    VendorVoucherGiftPayload,
    VendorVoucherRead,
    VendorVoucherRedemptionRead,
    VendorVoucherUpsert,
)
from app.services.email_service import (
    send_admin_voucher_review_email,
)
from app.services.sms_service import notify_admins_by_sms

logger = logging.getLogger(__name__)


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

    notify_admins_by_sms(
        db,
        feature="promotions",
        message=(
            f"ODOS: {store_title} created voucher {voucher.code} that needs approval "
            "before it can go live. Review in the admin panel."
        ),
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
