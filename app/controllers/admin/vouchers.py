"""Admin voucher management: CRUD, lifecycle, bulk generation, review.

Self-contained by design -- of the eighteen functions here, the only thing
reached for outside the domain is the admin guard.
"""

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.controllers.admin._shared import require_admin
from app.controllers.voucher_controller import (
    build_voucher_reward_text,
    validate_voucher_configuration,
    voucher_status,
)
from app.core.admin_pagination import paginate_scalars
from app.core.event_types import PROMO_CREATED, PROMO_DELETED, PROMO_UPDATED
from app.helpers.promo_audit import log_admin_promo_mutation
from app.models import (
    Store,
    User,
    Voucher,
    VoucherRedemption,
)
from app.schemas.admin import (
    AdminPromotionAnalyticsRead,
    AdminVoucherBulkGenerate,
    AdminVoucherRead,
    AdminVoucherReview,
    AdminVoucherUpsert,
)
from app.schemas.pagination import AdminPageRead

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

def _voucher_status(voucher: Voucher, redemption_count: int) -> str:
    now = datetime.now(UTC)
    return voucher_status(voucher, now=now, overall_count=redemption_count)


def _serialize_voucher(
    voucher: Voucher,
    *,
    store_name: str | None = None,
    redemption_count: int = 0,
    unique_user_count: int = 0,
    total_discount_amount: float = 0,
) -> AdminVoucherRead:
    return AdminVoucherRead(
        id=voucher.id,
        code=voucher.code,
        title=voucher.title,
        description=voucher.description,
        issuer_name=voucher.issuer_name,
        scope=voucher.scope,
        owner_type=getattr(voucher, "owner_type", "platform") or "platform",
        availability=voucher.availability,
        store_id=voucher.store_id,
        store_name=store_name,
        eligible_store_ids=getattr(voucher, "eligible_store_ids", None),
        reward_text=voucher.reward_text,
        discount_type=voucher.discount_type,
        discount_value=round(voucher.discount_value, 2),
        min_subtotal=round(voucher.min_subtotal, 2),
        max_discount=round(voucher.max_discount, 2) if voucher.max_discount is not None else None,
        usage_limit=voucher.usage_limit,
        per_user_limit=voucher.per_user_limit,
        is_active=voucher.is_active,
        status=_voucher_status(voucher, redemption_count),
        redemption_count=redemption_count,
        unique_user_count=unique_user_count,
        total_discount_amount=round(total_discount_amount, 2),
        starts_at=voucher.starts_at,
        ends_at=voucher.ends_at,
        created_at=voucher.created_at,
        approval_status=getattr(voucher, "approval_status", "approved"),
        campaign_tag=getattr(voucher, "campaign_tag", None),
        review_notes=getattr(voucher, "review_notes", None),
        created_by_user_id=getattr(voucher, "created_by_user_id", None),
        promotion_type=getattr(voucher, "promotion_type", "coupon") or "coupon",
        priority=int(getattr(voucher, "priority", 0) or 0),
        stackable=bool(getattr(voucher, "stackable", False)),
        exclusive_group=getattr(voucher, "exclusive_group", None),
        auto_apply=bool(getattr(voucher, "auto_apply", False)),
        bogo_buy_quantity=getattr(voucher, "bogo_buy_quantity", None),
        bogo_get_quantity=getattr(voucher, "bogo_get_quantity", None),
        bogo_get_discount_percent=getattr(voucher, "bogo_get_discount_percent", None),
        first_order_only=bool(getattr(voucher, "first_order_only", False)),
        new_user_only=bool(getattr(voucher, "new_user_only", False)),
        category_slugs=getattr(voucher, "category_slugs", None),
        excluded_category_slugs=getattr(voucher, "excluded_category_slugs", None),
        product_ids=getattr(voucher, "product_ids", None),
        excluded_product_ids=getattr(voucher, "excluded_product_ids", None),
    )


def _voucher_audit_snapshot(voucher: Voucher) -> dict:
    return {
        "code": voucher.code,
        "title": voucher.title,
        "promotion_type": getattr(voucher, "promotion_type", "coupon"),
        "discount_type": voucher.discount_type,
        "is_active": voucher.is_active,
        "auto_apply": bool(getattr(voucher, "auto_apply", False)),
        "priority": int(getattr(voucher, "priority", 0) or 0),
    }


def _apply_voucher_upsert(voucher: Voucher, payload: AdminVoucherUpsert, *, target_store: Store | None) -> None:
    discount_value = 0 if payload.discount_type in {"free_shipping", "bogo"} else round(payload.discount_value, 2)
    voucher.code = payload.code
    voucher.title = payload.title
    voucher.description = payload.description
    voucher.issuer_name = payload.issuer_name or (target_store.title if target_store else None)
    voucher.scope = payload.scope
    voucher.owner_type = payload.owner_type or "platform"
    voucher.availability = payload.availability
    voucher.store_id = target_store.id if target_store else None
    voucher.eligible_store_ids = (
        None if payload.scope == "store" else payload.eligible_store_ids
    )
    voucher.reward_text = build_voucher_reward_text(
        payload.discount_type,
        discount_value or payload.discount_value,
        promotion_type=payload.promotion_type,
        bogo_buy_quantity=payload.bogo_buy_quantity,
        bogo_get_quantity=payload.bogo_get_quantity,
        bogo_get_discount_percent=payload.bogo_get_discount_percent,
    )
    voucher.discount_type = payload.discount_type
    voucher.discount_value = discount_value
    voucher.min_subtotal = round(payload.min_subtotal, 2)
    voucher.max_discount = round(payload.max_discount, 2) if payload.max_discount is not None else None
    voucher.usage_limit = payload.usage_limit
    voucher.per_user_limit = payload.per_user_limit
    voucher.is_active = payload.is_active
    voucher.starts_at = payload.starts_at
    voucher.ends_at = payload.ends_at
    voucher.campaign_tag = payload.campaign_tag
    voucher.promotion_type = payload.promotion_type
    voucher.priority = payload.priority
    voucher.stackable = payload.stackable
    voucher.exclusive_group = payload.exclusive_group
    voucher.auto_apply = payload.auto_apply
    voucher.bogo_buy_quantity = payload.bogo_buy_quantity
    voucher.bogo_get_quantity = payload.bogo_get_quantity
    voucher.bogo_get_discount_percent = payload.bogo_get_discount_percent
    voucher.first_order_only = payload.first_order_only
    voucher.new_user_only = payload.new_user_only
    voucher.category_slugs = payload.category_slugs
    voucher.excluded_category_slugs = payload.excluded_category_slugs
    voucher.product_ids = payload.product_ids
    voucher.excluded_product_ids = payload.excluded_product_ids


def _validate_voucher_payload(payload: AdminVoucherUpsert) -> None:
    validate_voucher_configuration(
        scope=payload.scope,
        availability=payload.availability,
        discount_type=payload.discount_type,
        discount_value=payload.discount_value,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        usage_limit=payload.usage_limit,
        per_user_limit=payload.per_user_limit,
        store_id=payload.store_id,
        promotion_type=payload.promotion_type,
        owner_type=payload.owner_type or "platform",
        bogo_buy_quantity=payload.bogo_buy_quantity,
        bogo_get_quantity=payload.bogo_get_quantity,
        bogo_get_discount_percent=payload.bogo_get_discount_percent,
        category_slugs=payload.category_slugs,
        product_ids=payload.product_ids,
        eligible_store_ids=payload.eligible_store_ids,
        excluded_category_slugs=payload.excluded_category_slugs,
    )


def _voucher_stats_map(db: Session, voucher_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, float | int]]:
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
            "redemption_count": int(redemption_count or 0),
            "unique_user_count": int(unique_user_count or 0),
            "total_discount_amount": round(float(total_discount_amount or 0), 2),
        }
        for voucher_id, redemption_count, unique_user_count, total_discount_amount in rows
    }


def _serialize_admin_vouchers(db: Session, vouchers: list[Voucher]) -> list[AdminVoucherRead]:
    stats_map = _voucher_stats_map(db, [voucher.id for voucher in vouchers])
    store_name_map = dict(db.execute(
            select(Store.id, Store.title).where(
                Store.id.in_([voucher.store_id for voucher in vouchers if voucher.store_id])
            )
        ).all())
    return [
        _serialize_voucher(
            voucher,
            store_name=store_name_map.get(voucher.store_id or ""),
            redemption_count=int(stats_map.get(voucher.id, {}).get("redemption_count", 0)),
            unique_user_count=int(stats_map.get(voucher.id, {}).get("unique_user_count", 0)),
            total_discount_amount=float(
                stats_map.get(voucher.id, {}).get("total_discount_amount", 0)
            ),
        )
        for voucher in vouchers
    ]


def list_admin_vouchers(
    db: Session,
    current_user: User,
    *,
    limit: int = 30,
    offset: int = 0,
    q: str | None = None,
    status_filter: str | None = None,
    owner_type: str | None = None,
    scope: str | None = None,
) -> AdminPageRead[AdminVoucherRead]:
    require_admin(current_user)
    statement = select(Voucher).order_by(Voucher.created_at.desc(), Voucher.title.asc())
    if q:
        needle = f"%{q.strip().upper()}%"
        statement = statement.where(
            func.upper(Voucher.code).like(needle)
            | func.upper(Voucher.title).like(needle)
            | func.upper(func.coalesce(Voucher.campaign_tag, "")).like(needle)
        )
    if owner_type in {"platform", "vendor"}:
        statement = statement.where(Voucher.owner_type == owner_type)
    if scope in {"odos", "store", "category", "product"}:
        statement = statement.where(Voucher.scope == scope)

    # Status is derived; when filtering by status we page after computing it.
    if status_filter and status_filter != "all":
        vouchers = list(db.scalars(statement).all())
        serialized = _serialize_admin_vouchers(db, vouchers)
        filtered = [item for item in serialized if item.status == status_filter]
        page_items = filtered[offset : offset + limit]
        return AdminPageRead(
            items=page_items,
            has_more=offset + limit < len(filtered),
        )

    vouchers, has_more = paginate_scalars(db, statement, limit=limit, offset=offset)
    return AdminPageRead(
        items=_serialize_admin_vouchers(db, vouchers),
        has_more=has_more,
    )


def _get_admin_voucher(db: Session, voucher_id: str) -> Voucher:
    try:
        normalized_id = uuid.UUID(str(voucher_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Voucher not found.",
        ) from exc

    voucher = db.get(Voucher, normalized_id)
    if not voucher:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voucher not found.")
    return voucher


def create_admin_voucher(
    db: Session,
    current_user: User,
    payload: AdminVoucherUpsert,
) -> AdminVoucherRead:
    require_admin(current_user)
    _validate_voucher_payload(payload)

    target_store = None
    if payload.scope == "store":
        target_store = db.scalar(select(Store).where(Store.id == payload.store_id))
        if not target_store:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected store was not found.",
            )

    discount_value = 0 if payload.discount_type in {"free_shipping", "bogo"} else round(payload.discount_value, 2)
    voucher = Voucher(
        code=payload.code,
        title=payload.title,
        reward_text=build_voucher_reward_text(
            payload.discount_type,
            discount_value or payload.discount_value,
            promotion_type=payload.promotion_type,
            bogo_buy_quantity=payload.bogo_buy_quantity,
            bogo_get_quantity=payload.bogo_get_quantity,
            bogo_get_discount_percent=payload.bogo_get_discount_percent,
        ),
        discount_type=payload.discount_type,
        discount_value=discount_value,
        owner_type=payload.owner_type or "platform",
        approval_status="approved",
        created_by_user_id=current_user.id,
        reviewed_by_user_id=current_user.id,
    )
    _apply_voucher_upsert(voucher, payload, target_store=target_store)
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
    log_admin_promo_mutation(
        db,
        admin_user=current_user,
        event_type=PROMO_CREATED,
        action="promo.created",
        voucher=voucher,
        after_state=_voucher_audit_snapshot(voucher),
    )
    return _serialize_voucher(voucher, store_name=target_store.title if target_store else None)


def update_admin_voucher(
    db: Session,
    current_user: User,
    voucher_id: str,
    payload: AdminVoucherUpsert,
) -> AdminVoucherRead:
    require_admin(current_user)
    _validate_voucher_payload(payload)

    voucher = _get_admin_voucher(db, voucher_id)
    before_state = _voucher_audit_snapshot(voucher)
    target_store = None
    if payload.scope == "store":
        target_store = db.scalar(select(Store).where(Store.id == payload.store_id))
        if not target_store:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected store was not found.",
            )
    _apply_voucher_upsert(voucher, payload, target_store=target_store)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That voucher code already exists.",
        ) from exc

    db.refresh(voucher)
    log_admin_promo_mutation(
        db,
        admin_user=current_user,
        event_type=PROMO_UPDATED,
        action="promo.updated",
        voucher=voucher,
        before_state=before_state,
        after_state=_voucher_audit_snapshot(voucher),
    )
    stats_map = _voucher_stats_map(db, [voucher.id])
    voucher_stats = stats_map.get(voucher.id, {})
    return _serialize_voucher(
        voucher,
        store_name=target_store.title if target_store else None,
        redemption_count=int(voucher_stats.get("redemption_count", 0)),
        unique_user_count=int(voucher_stats.get("unique_user_count", 0)),
        total_discount_amount=float(voucher_stats.get("total_discount_amount", 0)),
    )


def archive_admin_voucher(
    db: Session,
    current_user: User,
    voucher_id: str,
) -> None:
    require_admin(current_user)
    voucher = _get_admin_voucher(db, voucher_id)
    before_state = _voucher_audit_snapshot(voucher)
    voucher.is_active = False
    db.commit()
    log_admin_promo_mutation(
        db,
        admin_user=current_user,
        event_type=PROMO_DELETED,
        action="promo.archived",
        voucher=voucher,
        before_state=before_state,
        after_state=_voucher_audit_snapshot(voucher),
    )


def pause_admin_voucher(
    db: Session,
    current_user: User,
    voucher_id: str,
) -> AdminVoucherRead:
    require_admin(current_user)
    voucher = _get_admin_voucher(db, voucher_id)
    before_state = _voucher_audit_snapshot(voucher)
    voucher.is_active = False
    db.commit()
    db.refresh(voucher)
    log_admin_promo_mutation(
        db,
        admin_user=current_user,
        event_type=PROMO_UPDATED,
        action="promo.paused",
        voucher=voucher,
        before_state=before_state,
        after_state=_voucher_audit_snapshot(voucher),
    )
    return _serialize_admin_vouchers(db, [voucher])[0]


def resume_admin_voucher(
    db: Session,
    current_user: User,
    voucher_id: str,
) -> AdminVoucherRead:
    require_admin(current_user)
    voucher = _get_admin_voucher(db, voucher_id)
    before_state = _voucher_audit_snapshot(voucher)
    if getattr(voucher, "approval_status", "approved") not in {"approved"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only approved vouchers can be resumed.",
        )
    voucher.is_active = True
    db.commit()
    db.refresh(voucher)
    log_admin_promo_mutation(
        db,
        admin_user=current_user,
        event_type=PROMO_UPDATED,
        action="promo.resumed",
        voucher=voucher,
        before_state=before_state,
        after_state=_voucher_audit_snapshot(voucher),
    )
    return _serialize_admin_vouchers(db, [voucher])[0]


def duplicate_admin_voucher(
    db: Session,
    current_user: User,
    voucher_id: str,
) -> AdminVoucherRead:
    require_admin(current_user)
    source = _get_admin_voucher(db, voucher_id)
    suffix = datetime.now(UTC).strftime("%H%M%S")
    base_code = (source.code or "COPY")[:32]
    new_code = f"{base_code}-C{suffix}"[:40]

    clone = Voucher(
        code=new_code,
        title=f"{source.title} (Copy)",
        description=source.description,
        issuer_name=source.issuer_name,
        scope=source.scope,
        owner_type=getattr(source, "owner_type", "platform") or "platform",
        availability=source.availability,
        store_id=source.store_id,
        eligible_store_ids=getattr(source, "eligible_store_ids", None),
        reward_text=source.reward_text,
        discount_type=source.discount_type,
        discount_value=source.discount_value,
        min_subtotal=source.min_subtotal,
        max_discount=source.max_discount,
        usage_limit=source.usage_limit,
        per_user_limit=source.per_user_limit,
        is_active=False,
        starts_at=source.starts_at,
        ends_at=source.ends_at,
        campaign_tag=source.campaign_tag,
        visibility=getattr(source, "visibility", "public"),
        approval_status="approved",
        created_by_user_id=current_user.id,
        reviewed_by_user_id=current_user.id,
        first_order_only=bool(getattr(source, "first_order_only", False)),
        new_user_only=bool(getattr(source, "new_user_only", False)),
        category_slugs=getattr(source, "category_slugs", None),
        excluded_category_slugs=getattr(source, "excluded_category_slugs", None),
        product_ids=getattr(source, "product_ids", None),
        excluded_product_ids=getattr(source, "excluded_product_ids", None),
        promotion_type=getattr(source, "promotion_type", "coupon") or "coupon",
        priority=int(getattr(source, "priority", 0) or 0),
        stackable=bool(getattr(source, "stackable", False)),
        exclusive_group=getattr(source, "exclusive_group", None),
        auto_apply=False,
        bogo_buy_quantity=getattr(source, "bogo_buy_quantity", None),
        bogo_get_quantity=getattr(source, "bogo_get_quantity", None),
        bogo_get_discount_percent=getattr(source, "bogo_get_discount_percent", None),
        rules_json=getattr(source, "rules_json", None),
    )
    db.add(clone)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not duplicate that voucher code. Try again.",
        ) from exc
    db.refresh(clone)
    log_admin_promo_mutation(
        db,
        admin_user=current_user,
        event_type=PROMO_CREATED,
        action="promo.duplicated",
        voucher=clone,
        after_state=_voucher_audit_snapshot(clone),
    )
    return _serialize_admin_vouchers(db, [clone])[0]


def bulk_generate_admin_vouchers(
    db: Session,
    current_user: User,
    payload: AdminVoucherBulkGenerate,
) -> list[AdminVoucherRead]:
    require_admin(current_user)
    _validate_voucher_payload(payload.template)

    target_store = None
    if payload.template.scope == "store":
        target_store = db.scalar(select(Store).where(Store.id == payload.template.store_id))
        if not target_store:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected store was not found.",
            )

    created: list[AdminVoucherRead] = []
    for index in range(payload.count):
        code = f"{payload.prefix}{index + 1:04d}"
        item_payload = payload.template.model_copy(update={"code": code})
        voucher = Voucher(
            code=code,
            title=item_payload.title,
            reward_text=build_voucher_reward_text(
                item_payload.discount_type,
                item_payload.discount_value,
                promotion_type=item_payload.promotion_type,
                bogo_buy_quantity=item_payload.bogo_buy_quantity,
                bogo_get_quantity=item_payload.bogo_get_quantity,
                bogo_get_discount_percent=item_payload.bogo_get_discount_percent,
            ),
            discount_type=item_payload.discount_type,
            discount_value=item_payload.discount_value,
            approval_status="approved",
            reviewed_by_user_id=current_user.id,
        )
        _apply_voucher_upsert(voucher, item_payload, target_store=target_store)
        db.add(voucher)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not generate code {code}.",
            ) from exc
        log_admin_promo_mutation(
            db,
            admin_user=current_user,
            event_type=PROMO_CREATED,
            action="promo.bulk_created",
            voucher=voucher,
            after_state=_voucher_audit_snapshot(voucher),
        )
        created.append(_serialize_voucher(voucher, store_name=target_store.title if target_store else None))

    db.commit()
    return created


def get_admin_promotion_analytics(
    db: Session,
    current_user: User,
) -> AdminPromotionAnalyticsRead:
    require_admin(current_user)
    vouchers = list(db.scalars(select(Voucher).order_by(Voucher.created_at.desc())).all())
    stats_map = _voucher_stats_map(db, [voucher.id for voucher in vouchers])

    total_redemptions = 0
    total_discount_given = 0.0
    active_campaigns = 0
    serialized: list[AdminVoucherRead] = []

    for voucher in vouchers:
        stats = stats_map.get(voucher.id, {})
        redemption_count = int(stats.get("redemption_count", 0))
        total_redemptions += redemption_count
        total_discount_given += float(stats.get("total_discount_amount", 0))
        status_value = _voucher_status(voucher, redemption_count)
        if status_value == "active":
            active_campaigns += 1
        serialized.append(
            _serialize_voucher(
                voucher,
                redemption_count=redemption_count,
                unique_user_count=int(stats.get("unique_user_count", 0)),
                total_discount_amount=float(stats.get("total_discount_amount", 0)),
            )
        )

    top_campaigns = sorted(
        serialized,
        key=lambda item: (item.total_discount_amount, item.redemption_count),
        reverse=True,
    )[:10]

    return AdminPromotionAnalyticsRead(
        total_campaigns=len(vouchers),
        active_campaigns=active_campaigns,
        total_redemptions=total_redemptions,
        total_discount_given=round(total_discount_given, 2),
        top_campaigns=top_campaigns,
    )


def review_admin_voucher(
    db: Session,
    current_user: User,
    voucher_id: str,
    payload: AdminVoucherReview,
) -> AdminVoucherRead:
    require_admin(current_user)
    voucher = _get_admin_voucher(db, voucher_id)

    if payload.approval_status not in {"approved", "rejected", "disabled"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Approval status must be approved, rejected, or disabled.",
        )

    voucher.approval_status = payload.approval_status
    voucher.reviewed_by_user_id = current_user.id
    voucher.review_notes = payload.review_notes
    if payload.approval_status == "approved":
        voucher.is_active = payload.is_active if payload.is_active is not None else True
    elif payload.approval_status in {"rejected", "disabled"}:
        voucher.is_active = False

    db.commit()
    db.refresh(voucher)

    store_name = None
    if voucher.store_id:
        store_name = db.scalar(select(Store.title).where(Store.id == voucher.store_id))
    stats_map = _voucher_stats_map(db, [voucher.id])
    voucher_stats = stats_map.get(voucher.id, {})
    return _serialize_voucher(
        voucher,
        store_name=store_name,
        redemption_count=int(voucher_stats.get("redemption_count", 0)),
        unique_user_count=int(voucher_stats.get("unique_user_count", 0)),
        total_discount_amount=float(voucher_stats.get("total_discount_amount", 0)),
    )
