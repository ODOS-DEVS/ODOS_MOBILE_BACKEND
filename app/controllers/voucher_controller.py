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
    StoreVoucherRead,
    VoucherPreviewRead,
    VoucherPreviewRequest,
    VoucherSuggestionsRequest,
    VoucherWalletRead,
)

SUPPORTED_VOUCHER_DISCOUNT_TYPES = {"percent", "fixed", "free_shipping"}
SUPPORTED_VOUCHER_SCOPES = {"odos", "store"}
SUPPORTED_VOUCHER_AVAILABILITY = {"auto", "claim", "assigned"}


@dataclass(slots=True)
class VoucherQuote:
    voucher: Voucher
    discount_amount: float
    eligible_subtotal_amount: float
    subtotal_amount: float
    shipping_amount: float
    total_amount: float
    store_name: str | None = None


def _compute_subtotal(items: list[OrderItemCreate]) -> float:
    return round(sum(item.unit_price * item.quantity for item in items), 2)


def _normalize_code(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip().upper()
    return cleaned or None


def build_voucher_reward_text(discount_type: str, discount_value: float) -> str:
    if discount_type == "percent":
        value = int(discount_value) if float(discount_value).is_integer() else round(discount_value, 2)
        return f"{value}% OFF"
    if discount_type == "fixed":
        value = int(discount_value) if float(discount_value).is_integer() else round(discount_value, 2)
        return f"GHS {value} OFF"
    return "FREE SHIPPING"


def validate_voucher_configuration(
    *,
    scope: str,
    availability: str,
    discount_type: str,
    discount_value: float,
    starts_at: datetime | None,
    ends_at: datetime | None,
    usage_limit: int | None,
    per_user_limit: int | None,
    store_id: str | None,
) -> None:
    if scope not in SUPPORTED_VOUCHER_SCOPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported voucher scope.",
        )
    if availability not in SUPPORTED_VOUCHER_AVAILABILITY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported voucher availability mode.",
        )
    if discount_type not in SUPPORTED_VOUCHER_DISCOUNT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported voucher discount type.",
        )
    if discount_type == "percent" and discount_value > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Percent vouchers cannot exceed 100%.",
        )
    if scope == "store" and not store_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Store promotions must be attached to a store.",
        )
    if scope == "store" and discount_type == "free_shipping":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Store promotions do not support free shipping yet.",
        )
    if starts_at and ends_at and ends_at < starts_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Voucher end date must be after the start date.",
        )
    if usage_limit is not None and per_user_limit is not None and per_user_limit > usage_limit:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Per-user limit cannot be higher than the total usage limit.",
        )


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
    if not voucher.is_active:
        return "disabled"
    if voucher.starts_at and voucher.starts_at > now:
        return "scheduled"
    if voucher.ends_at and voucher.ends_at < now:
        return "expired"
    if voucher.usage_limit is not None and overall_count >= voucher.usage_limit:
        return "limit_reached"
    return "active"


def _discount_for_voucher(voucher: Voucher, eligible_subtotal: float, shipping_amount: float) -> float:
    if voucher.discount_type == "percent":
        discount = eligible_subtotal * (voucher.discount_value / 100)
    elif voucher.discount_type == "fixed":
        discount = voucher.discount_value
    elif voucher.discount_type == "free_shipping":
        discount = shipping_amount
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="That voucher is configured with an unsupported discount type.",
        )

    if voucher.max_discount is not None:
        discount = min(discount, voucher.max_discount)

    ceiling = eligible_subtotal + (shipping_amount if voucher.discount_type == "free_shipping" else 0)
    return round(max(0, min(discount, ceiling)), 2)


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
    from app.services.promotion_service import normalize_voucher_code, validate_voucher_for_checkout

    normalized_code = normalize_voucher_code(voucher_code)
    if not normalized_code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Enter a promo code first.",
        )

    voucher = db.scalar(select(Voucher).where(Voucher.code == normalized_code))
    if not voucher:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That promo code wasn't found.",
        )

    return validate_voucher_for_checkout(db, user, voucher, items, shipping_amount)


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


def list_public_promotions(db: Session) -> list[StoreVoucherRead]:
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
    wallet = list_user_vouchers(db, user)
    active_codes = [item.code for item in wallet if item.status == "active"]
    if not active_codes:
        return []

    suggestions: list[VoucherPreviewRead] = []
    for code in active_codes:
        try:
            preview = preview_voucher(
                db,
                user,
                VoucherPreviewRequest(
                    voucher_code=code,
                    items=payload.items,
                    shipping_amount=payload.shipping_amount,
                ),
            )
            suggestions.append(preview)
        except HTTPException:
            continue

    suggestions.sort(key=lambda item: item.discount_amount, reverse=True)
    return suggestions[:5]
