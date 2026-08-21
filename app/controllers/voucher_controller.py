from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product, Store, User, Voucher, VoucherAssignment, VoucherRedemption
from app.schemas.order import OrderItemCreate
from app.schemas.voucher import (
    AppliedPromotionRead,
    PromotionCalculateRead,
    PromotionCalculateRequest,
    RejectedPromotionRead,
    StoreVoucherRead,
    VoucherPreviewRead,
    VoucherPreviewRequest,
    VoucherSuggestionsRequest,
    VoucherWalletRead,
)
from app.services.promotion_engine import (
    calculate_best_discount,
    calculate_voucher_quote,
    suggest_best_promotions,
)
from app.services.promotion_service import (
    build_voucher_reward_text,
    validate_voucher_configuration,
    voucher_status,
    VoucherQuote,
)


def _compute_subtotal(items: list[OrderItemCreate]) -> float:
    return round(sum(item.unit_price * item.quantity for item in items), 2)


def _normalize_code(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip().upper()
    return cleaned or None


def build_voucher_reward_text(
    discount_type: str,
    discount_value: float,
    *,
    promotion_type: str = "coupon",
    bogo_buy_quantity: int | None = None,
    bogo_get_quantity: int | None = None,
    bogo_get_discount_percent: float | None = None,
) -> str:
    from app.services.promotion_service import build_voucher_reward_text as _build

    return _build(
        discount_type,
        discount_value,
        promotion_type=promotion_type,
        bogo_buy_quantity=bogo_buy_quantity,
        bogo_get_quantity=bogo_get_quantity,
        bogo_get_discount_percent=bogo_get_discount_percent,
    )


def validate_voucher_configuration(**kwargs) -> None:
    from app.services.promotion_service import validate_voucher_configuration as _validate

    _validate(**kwargs)


def _counts_for_voucher(db: Session, voucher_id, user_id) -> tuple[int, int]:
    overall = db.scalar(
        select(func.count(VoucherRedemption.id)).where(VoucherRedemption.voucher_id == voucher_id)
    )
    user_count = db.scalar(
        select(func.count(VoucherRedemption.id)).where(
            VoucherRedemption.voucher_id == voucher_id,
            VoucherRedemption.user_id == user_id,
        )
    )
    return int(overall or 0), int(user_count or 0)


def voucher_status(voucher: Voucher, *, now: datetime, overall_count: int) -> str:
    from app.services.promotion_service import voucher_status as _status

    return _status(voucher, now=now, overall_count=overall_count)


def _discount_for_voucher(voucher: Voucher, eligible_subtotal: float, shipping_amount: float) -> float:
    from app.services.promotion_service import discount_for_voucher

    return discount_for_voucher(voucher, eligible_subtotal, shipping_amount)


def _voucher_store_name_map(db: Session, store_ids: Iterable[str]) -> dict[str, str]:
    unique_ids = [store_id for store_id in dict.fromkeys(store_ids) if store_id]
    if not unique_ids:
        return {}

    rows = db.execute(select(Store.id, Store.title).where(Store.id.in_(unique_ids))).all()
    return {store_id: title for store_id, title in rows}


def _assignment_for_user(db: Session, voucher_id: uuid.UUID, user_id: uuid.UUID) -> VoucherAssignment | None:
    return db.scalar(
        select(VoucherAssignment).where(
            VoucherAssignment.voucher_id == voucher_id,
            VoucherAssignment.user_id == user_id,
        )
    )


def _assignment_source_map(
    db: Session,
    voucher_ids: list[uuid.UUID],
    user_id: uuid.UUID,
) -> dict[uuid.UUID, VoucherAssignment]:
    if not voucher_ids:
        return {}

    rows = list(
        db.scalars(
            select(VoucherAssignment).where(
                VoucherAssignment.voucher_id.in_(voucher_ids),
                VoucherAssignment.user_id == user_id,
            )
        ).all()
    )
    return {row.voucher_id: row for row in rows}


def _wallet_status(voucher: Voucher, *, now: datetime, overall_count: int, user_count: int) -> str:
    current_status = voucher_status(voucher, now=now, overall_count=overall_count)
    if current_status != "active":
        return "expired"
    if voucher.per_user_limit is not None and user_count >= voucher.per_user_limit:
        return "used"
    return "active"


def _serialize_wallet_voucher(
    voucher: Voucher,
    *,
    status_value: str,
    store_name: str | None,
    source: str | None,
) -> VoucherWalletRead:
    return VoucherWalletRead(
        id=voucher.id,
        code=voucher.code,
        title=voucher.title,
        description=voucher.description,
        issuer_name=voucher.issuer_name,
        scope=voucher.scope,
        availability=voucher.availability,
        store_id=voucher.store_id,
        store_name=store_name,
        reward_text=voucher.reward_text,
        min_subtotal=round(voucher.min_subtotal, 2),
        expires_at=voucher.ends_at,
        status=status_value,  # type: ignore[arg-type]
        source=source,
    )


def _serialize_store_voucher(
    voucher: Voucher,
    *,
    store_name: str | None,
    claimed: bool,
) -> StoreVoucherRead:
    return StoreVoucherRead(
        id=voucher.id,
        code=voucher.code,
        title=voucher.title,
        description=voucher.description,
        issuer_name=voucher.issuer_name,
        scope=voucher.scope,  # type: ignore[arg-type]
        availability=voucher.availability,  # type: ignore[arg-type]
        store_id=voucher.store_id,
        store_name=store_name,
        reward_text=voucher.reward_text,
        min_subtotal=round(voucher.min_subtotal, 2),
        expires_at=voucher.ends_at,
        claimed=claimed,
        campaign_tag=getattr(voucher, "campaign_tag", None),
        discount_type=voucher.discount_type,
        approval_status=getattr(voucher, "approval_status", None),
    )


def _voucher_by_id(db: Session, voucher_id: str | uuid.UUID) -> Voucher:
    try:
        normalized_id = voucher_id if isinstance(voucher_id, uuid.UUID) else uuid.UUID(str(voucher_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Voucher not found.",
        ) from exc

    voucher = db.get(Voucher, normalized_id)
    if not voucher:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Voucher not found.")
    return voucher


def _product_store_map(db: Session, items: list[OrderItemCreate]) -> dict[str, str | None]:
    product_ids = [item.product_id for item in items]
    if not product_ids:
        return {}

    rows = db.execute(select(Product.id, Product.store_id).where(Product.id.in_(product_ids))).all()
    return {product_id: store_id for product_id, store_id in rows}


def _eligible_subtotal_for_voucher(
    voucher: Voucher,
    items: list[OrderItemCreate],
    product_store_map: dict[str, str | None],
) -> float:
    if voucher.scope == "odos":
        return _compute_subtotal(items)

    return round(
        sum(
            item.unit_price * item.quantity
            for item in items
            if product_store_map.get(item.product_id) == voucher.store_id
        ),
        2,
    )


def build_voucher_quote(
    db: Session,
    user: User,
    voucher_code: str,
    items: list[OrderItemCreate],
    shipping_amount: float,
) -> VoucherQuote:
    return calculate_voucher_quote(db, user, voucher_code, items, shipping_amount)


def list_user_vouchers(db: Session, user: User) -> list[VoucherWalletRead]:
    auto_vouchers = list(
        db.scalars(
            select(Voucher).where(
                Voucher.availability == "auto",
                Voucher.approval_status == "approved",
                Voucher.is_active.is_(True),
            )
            .order_by(Voucher.created_at.desc(), Voucher.title.asc())
        ).all()
    )
    assigned_vouchers = list(
        db.scalars(
            select(Voucher)
            .join(VoucherAssignment, VoucherAssignment.voucher_id == Voucher.id)
            .where(VoucherAssignment.user_id == user.id)
            .order_by(Voucher.created_at.desc(), Voucher.title.asc())
        ).all()
    )

    voucher_map: dict[uuid.UUID, Voucher] = {}
    for voucher in [*assigned_vouchers, *auto_vouchers]:
        voucher_map[voucher.id] = voucher

    vouchers = list(voucher_map.values())
    if not vouchers:
        return []

    voucher_ids = [voucher.id for voucher in vouchers]
    usage_rows = db.execute(
        select(VoucherRedemption.voucher_id, func.count(VoucherRedemption.id))
        .where(VoucherRedemption.voucher_id.in_(voucher_ids))
        .group_by(VoucherRedemption.voucher_id)
    ).all()
    user_rows = db.execute(
        select(VoucherRedemption.voucher_id, func.count(VoucherRedemption.id))
        .where(
            VoucherRedemption.voucher_id.in_(voucher_ids),
            VoucherRedemption.user_id == user.id,
        )
        .group_by(VoucherRedemption.voucher_id)
    ).all()

    overall_map = {voucher_id: int(count) for voucher_id, count in usage_rows}
    user_map = {voucher_id: int(count) for voucher_id, count in user_rows}
    assignment_map = _assignment_source_map(db, voucher_ids, user.id)
    store_map = _voucher_store_name_map(
        db,
        [voucher.store_id for voucher in vouchers if voucher.store_id],
    )
    now = datetime.now(timezone.utc)

    payloads: list[VoucherWalletRead] = []
    for voucher in vouchers:
        status_value = _wallet_status(
            voucher,
            now=now,
            overall_count=overall_map.get(voucher.id, 0),
            user_count=user_map.get(voucher.id, 0),
        )
        source = assignment_map.get(voucher.id).source if voucher.id in assignment_map else None
        payloads.append(
            _serialize_wallet_voucher(
                voucher,
                status_value=status_value,
                store_name=store_map.get(voucher.store_id or ""),
                source=source,
            )
        )

    return payloads


def list_public_promotions(db: Session, user_id: str | None = None) -> list[StoreVoucherRead]:
    from app.services.promotion_service import is_voucher_publicly_listable

    vouchers = list(
        db.scalars(
            select(Voucher)
            .where(
                Voucher.scope.in_(("odos", "category")),
                Voucher.availability.in_(("auto", "claim")),
            )
            .order_by(Voucher.created_at.desc(), Voucher.title.asc())
        ).all()
    )
    if not vouchers:
        return []

    usage_rows = db.execute(
        select(VoucherRedemption.voucher_id, func.count(VoucherRedemption.id))
        .where(VoucherRedemption.voucher_id.in_([voucher.id for voucher in vouchers]))
        .group_by(VoucherRedemption.voucher_id)
    ).all()
    overall_map = {voucher_id: int(count) for voucher_id, count in usage_rows}
    now = datetime.now(timezone.utc)

    payloads: list[StoreVoucherRead] = []
    for voucher in vouchers:
        if not is_voucher_publicly_listable(voucher):
            continue
        if voucher_status(voucher, now=now, overall_count=overall_map.get(voucher.id, 0)) != "active":
            continue
        if user_id is not None:
            if getattr(voucher, "first_order_only", False):
                from app.services.promotion_service import _user_has_prior_orders
                if _user_has_prior_orders(db, user_id):
                    continue
            if getattr(voucher, "new_user_only", False):
                user = db.scalar(select(User).where(User.id == user_id))
                if user and user.created_at:
                    account_age_days = (now - user.created_at).days
                    if account_age_days > 30:
                        continue
            eligibility_rules_dict = getattr(voucher, "eligibility_rules", None)
            if eligibility_rules_dict:
                from app.services.eligibility_service import parse_eligibility_rules, compute_user_eligibility_stats, evaluate_eligibility
                rules = parse_eligibility_rules(eligibility_rules_dict)
                if rules:
                    stats = compute_user_eligibility_stats(db, user_id)
                    ok, _ = evaluate_eligibility(rules, stats, now=now)
                    if not ok:
                        continue
        payloads.append(
            _serialize_store_voucher(
                voucher,
                store_name=None,
                claimed=False,
            )
        )

    return payloads


def list_store_vouchers(
    db: Session,
    store_id: str,
    *,
    user: User | None = None,
) -> list[StoreVoucherRead]:
    store = db.scalar(select(Store).where(Store.id == store_id))
    if not store:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found.")

    vouchers = list(
        db.scalars(
            select(Voucher)
            .where(
                Voucher.scope == "store",
                Voucher.store_id == store_id,
                Voucher.availability.in_(("auto", "claim")),
            )
            .order_by(Voucher.created_at.desc(), Voucher.title.asc())
        ).all()
    )
    if not vouchers:
        return []

    assignment_map: dict[uuid.UUID, VoucherAssignment] = {}
    if user:
        assignment_map = _assignment_source_map(db, [voucher.id for voucher in vouchers], user.id)

    usage_rows = db.execute(
        select(VoucherRedemption.voucher_id, func.count(VoucherRedemption.id))
        .where(VoucherRedemption.voucher_id.in_([voucher.id for voucher in vouchers]))
        .group_by(VoucherRedemption.voucher_id)
    ).all()
    overall_map = {voucher_id: int(count) for voucher_id, count in usage_rows}
    now = datetime.now(timezone.utc)

    payloads: list[StoreVoucherRead] = []
    for voucher in vouchers:
        if getattr(voucher, "approval_status", "approved") != "approved":
            continue
        if voucher_status(voucher, now=now, overall_count=overall_map.get(voucher.id, 0)) != "active":
            continue
        payloads.append(
            _serialize_store_voucher(
                voucher,
                store_name=store.title,
                claimed=voucher.id in assignment_map,
            )
        )

    return payloads


def claim_voucher(db: Session, user: User, voucher_id: str) -> VoucherWalletRead:
    voucher = _voucher_by_id(db, voucher_id)
    overall_count, user_count = _counts_for_voucher(db, voucher.id, user.id)
    now = datetime.now(timezone.utc)

    if voucher_status(voucher, now=now, overall_count=overall_count) != "active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That promotion is not currently available.",
        )
    if voucher.availability == "assigned":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This promotion is sent directly to selected shoppers and cannot be claimed publicly.",
        )

    assignment = _assignment_for_user(db, voucher.id, user.id)
    if not assignment:
        assignment = VoucherAssignment(
            voucher_id=voucher.id,
            user_id=user.id,
            source="claim",
        )
        db.add(assignment)
        db.commit()
        db.refresh(assignment)

    status_value = _wallet_status(
        voucher,
        now=now,
        overall_count=overall_count,
        user_count=user_count,
    )
    store_name = None
    if voucher.store_id:
        store_name = db.scalar(select(Store.title).where(Store.id == voucher.store_id))
    return _serialize_wallet_voucher(
        voucher,
        status_value=status_value,
        store_name=store_name,
        source=assignment.source,
    )


def assign_voucher_to_user(
    db: Session,
    *,
    voucher: Voucher,
    recipient: User,
    source: str,
    assigned_by_user_id: uuid.UUID | None,
    note: str | None = None,
) -> VoucherAssignment:
    assignment = _assignment_for_user(db, voucher.id, recipient.id)
    if assignment:
        return assignment

    assignment = VoucherAssignment(
        voucher_id=voucher.id,
        user_id=recipient.id,
        source=source,
        assigned_by_user_id=assigned_by_user_id,
        note=note,
    )
    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


def preview_voucher(db: Session, user: User, payload: VoucherPreviewRequest) -> VoucherPreviewRead:
    quote = build_voucher_quote(
        db,
        user,
        payload.voucher_code,
        payload.items,
        payload.shipping_amount,
    )

    return VoucherPreviewRead(
        voucher_id=quote.voucher.id,
        code=quote.voucher.code,
        title=quote.voucher.title,
        issuer_name=quote.voucher.issuer_name,
        scope=quote.voucher.scope,  # type: ignore[arg-type]
        availability=quote.voucher.availability,  # type: ignore[arg-type]
        store_id=quote.voucher.store_id,
        store_name=quote.store_name,
        reward_text=quote.voucher.reward_text,
        discount_amount=quote.discount_amount,
        eligible_subtotal_amount=quote.eligible_subtotal_amount,
        subtotal_amount=quote.subtotal_amount,
        shipping_amount=quote.shipping_amount,
        total_amount=quote.total_amount,
    )


def suggest_vouchers(
    db: Session,
    user: User,
    payload: VoucherSuggestionsRequest,
) -> list[VoucherPreviewRead]:
    suggestions: list[VoucherPreviewRead] = []
    for result in suggest_best_promotions(
        db,
        user,
        payload.items,
        payload.shipping_amount,
    ):
        if not result.primary_voucher:
            continue
        voucher = result.primary_voucher
        suggestions.append(
            VoucherPreviewRead(
                voucher_id=voucher.id,
                code=voucher.code,
                title=voucher.title,
                issuer_name=voucher.issuer_name,
                scope=voucher.scope,  # type: ignore[arg-type]
                availability=voucher.availability,  # type: ignore[arg-type]
                store_id=voucher.store_id,
                store_name=None,
                reward_text=voucher.reward_text,
                discount_amount=result.discount_amount,
                eligible_subtotal_amount=result.eligible_subtotal_amount,
                subtotal_amount=result.subtotal_amount,
                shipping_amount=result.shipping_amount,
                total_amount=result.total_amount,
            )
        )

    return suggestions


def calculate_promotions(
    db: Session,
    user: User,
    payload: PromotionCalculateRequest,
) -> PromotionCalculateRead:
    from app.helpers.promo_audit import log_promo_applied, log_promo_rejections

    result = calculate_best_discount(
        db,
        user,
        payload.items,
        payload.shipping_amount,
        voucher_code=payload.voucher_code,
        include_auto_apply=payload.include_auto_apply,
    )
    if result.rejected_promotions:
        log_promo_rejections(
            db,
            user=user,
            rejections=result.rejected_promotions,
            voucher_code=payload.voucher_code,
        )
    if result.applied_promotions:
        log_promo_applied(db, user=user, order=None, result=result)

    return PromotionCalculateRead(
        subtotal_amount=result.subtotal_amount,
        shipping_amount=result.shipping_amount,
        discount_amount=result.discount_amount,
        total_amount=result.total_amount,
        eligible_subtotal_amount=result.eligible_subtotal_amount,
        applied_promotions=[
            AppliedPromotionRead(
                voucher_id=uuid.UUID(promo.voucher_id),
                code=promo.code,
                title=promo.title,
                promotion_type=promo.promotion_type,
                discount_type=promo.discount_type,
                discount_amount=promo.discount_amount,
                priority=promo.priority,
                stackable=promo.stackable,
            )
            for promo in result.applied_promotions
        ],
        rejected_promotions=[
            RejectedPromotionRead(
                code=rejected.code,
                voucher_id=uuid.UUID(rejected.voucher_id) if rejected.voucher_id else None,
                reason=rejected.reason,
                rejection_code=rejected.rejection_code,
            )
            for rejected in result.rejected_promotions
        ],
    )
