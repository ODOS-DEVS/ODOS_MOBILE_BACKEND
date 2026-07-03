"""Enterprise promotion calculation engine.

All checkout discount decisions flow through calculate_best_discount().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User, Voucher, VoucherAssignment
from app.schemas.order import OrderItemCreate
from app.services.finance_math import round_money
from app.services.promotion_service import (
    VoucherQuote,
    normalize_voucher_code,
    validate_voucher_for_checkout,
)

PROMOTION_TYPES = frozenset(
    {"coupon", "automatic", "product", "cart", "bogo", "free_shipping"}
)


@dataclass(slots=True)
class LineDiscountBreakdown:
    product_id: str
    quantity: int
    unit_price: float
    discount_amount: float
    promotion_code: str | None = None
    promotion_id: str | None = None


@dataclass(slots=True)
class AppliedPromotion:
    voucher_id: str
    code: str
    title: str
    promotion_type: str
    discount_type: str
    discount_amount: float
    priority: int
    stackable: bool
    eligible_subtotal_amount: float = 0.0


@dataclass(slots=True)
class RejectedPromotion:
    code: str | None
    voucher_id: str | None
    reason: str
    rejection_code: str


@dataclass(slots=True)
class PromotionCalculationResult:
    subtotal_amount: float
    shipping_amount: float
    discount_amount: float
    total_amount: float
    applied_promotions: list[AppliedPromotion] = field(default_factory=list)
    rejected_promotions: list[RejectedPromotion] = field(default_factory=list)
    line_breakdown: list[LineDiscountBreakdown] = field(default_factory=list)
    primary_voucher: Voucher | None = None
    eligible_subtotal_amount: float = 0.0

    def to_breakdown_json(self) -> dict[str, Any]:
        return {
            "subtotal_amount": self.subtotal_amount,
            "shipping_amount": self.shipping_amount,
            "discount_amount": self.discount_amount,
            "total_amount": self.total_amount,
            "applied_promotions": [
                {
                    "voucher_id": promo.voucher_id,
                    "code": promo.code,
                    "title": promo.title,
                    "promotion_type": promo.promotion_type,
                    "discount_type": promo.discount_type,
                    "discount_amount": promo.discount_amount,
                    "priority": promo.priority,
                    "stackable": promo.stackable,
                }
                for promo in self.applied_promotions
            ],
            "rejected_promotions": [
                {
                    "code": rejected.code,
                    "voucher_id": rejected.voucher_id,
                    "reason": rejected.reason,
                    "rejection_code": rejected.rejection_code,
                }
                for rejected in self.rejected_promotions
            ],
        }


def _quote_to_applied(quote: VoucherQuote) -> AppliedPromotion:
    voucher = quote.voucher
    return AppliedPromotion(
        voucher_id=str(voucher.id),
        code=voucher.code,
        title=voucher.title,
        promotion_type=getattr(voucher, "promotion_type", "coupon") or "coupon",
        discount_type=voucher.discount_type,
        discount_amount=quote.discount_amount,
        priority=int(getattr(voucher, "priority", 0) or 0),
        stackable=bool(getattr(voucher, "stackable", False)),
        eligible_subtotal_amount=quote.eligible_subtotal_amount,
    )


def _rejection_from_exception(
    voucher: Voucher | None,
    *,
    code: str | None,
    exc: HTTPException,
) -> RejectedPromotion:
    detail = str(exc.detail)
    rejection_code = "invalid"
    lowered = detail.lower()
    if "expired" in lowered:
        rejection_code = "expired"
    elif "usage limit" in lowered or "already been used" in lowered:
        rejection_code = "usage_limit"
    elif "not active yet" in lowered or "scheduled" in lowered:
        rejection_code = "scheduled"
    elif "not found" in lowered or "wasn't found" in lowered:
        rejection_code = "not_found"
    elif "does not apply" in lowered or "eligible" in lowered:
        rejection_code = "ineligible"
    elif "wallet" in lowered or "gifted" in lowered:
        rejection_code = "not_assigned"
    elif "first order" in lowered or "new odos" in lowered:
        rejection_code = "user_ineligible"
    elif "review" in lowered:
        rejection_code = "pending_review"

    return RejectedPromotion(
        code=code or (voucher.code if voucher else None),
        voucher_id=str(voucher.id) if voucher else None,
        reason=detail,
        rejection_code=rejection_code,
    )


def try_evaluate_voucher(
    db: Session,
    user: User,
    voucher: Voucher,
    items: list[OrderItemCreate],
    shipping_amount: float,
) -> tuple[VoucherQuote | None, RejectedPromotion | None]:
    try:
        quote = validate_voucher_for_checkout(db, user, voucher, items, shipping_amount)
        return quote, None
    except HTTPException as exc:
        return None, _rejection_from_exception(voucher, code=voucher.code, exc=exc)


def _load_manual_voucher(db: Session, voucher_code: str | None) -> Voucher | None:
    normalized = normalize_voucher_code(voucher_code)
    if not normalized:
        return None
    return db.scalar(select(Voucher).where(Voucher.code == normalized))


def _load_auto_apply_candidates(db: Session) -> list[Voucher]:
    return list(
        db.scalars(
            select(Voucher)
            .where(
                Voucher.auto_apply.is_(True),
                Voucher.is_active.is_(True),
                Voucher.approval_status == "approved",
            )
            .order_by(Voucher.priority.desc(), Voucher.created_at.desc())
        ).all()
    )


def _wallet_candidate_codes(db: Session, user: User) -> list[str]:
    auto_rows = list(
        db.scalars(
            select(Voucher.code).where(
                Voucher.availability == "auto",
                Voucher.approval_status == "approved",
                Voucher.is_active.is_(True),
            )
        ).all()
    )
    assigned_rows = list(
        db.scalars(
            select(Voucher.code)
            .join(VoucherAssignment, VoucherAssignment.voucher_id == Voucher.id)
            .where(VoucherAssignment.user_id == user.id)
        ).all()
    )
    merged: list[str] = []
    for code in [*assigned_rows, *auto_rows]:
        if code and code not in merged:
            merged.append(code)
    return merged


def _resolve_promotion_set(
    candidates: list[tuple[Voucher, VoucherQuote]],
) -> list[tuple[Voucher, VoucherQuote]]:
    if not candidates:
        return []

    ranked = sorted(
        candidates,
        key=lambda pair: (
            int(getattr(pair[0], "priority", 0) or 0),
            pair[1].discount_amount,
        ),
        reverse=True,
    )

    selected: list[tuple[Voucher, VoucherQuote]] = []
    used_groups: set[str] = set()

    for voucher, quote in ranked:
        group = (getattr(voucher, "exclusive_group", None) or "").strip()
        stackable = bool(getattr(voucher, "stackable", False))

        if group and group in used_groups:
            continue

        if not selected:
            selected.append((voucher, quote))
            if group:
                used_groups.add(group)
            if not stackable:
                return selected
            continue

        if not stackable:
            continue

        if group:
            used_groups.add(group)
        selected.append((voucher, quote))

    return selected


def _merge_quotes(
    base_subtotal: float,
    shipping_amount: float,
    selected: list[tuple[Voucher, VoucherQuote]],
) -> PromotionCalculationResult:
    applied: list[AppliedPromotion] = []
    total_discount = 0.0
    remaining_shipping = shipping_amount
    remaining_subtotal = base_subtotal

    for voucher, quote in selected:
        promo = _quote_to_applied(quote)
        if voucher.discount_type == "free_shipping":
            promo.discount_amount = round_money(min(promo.discount_amount, remaining_shipping))
            remaining_shipping = round_money(max(0, remaining_shipping - promo.discount_amount))
        else:
            promo.discount_amount = round_money(min(promo.discount_amount, remaining_subtotal))
            remaining_subtotal = round_money(max(0, remaining_subtotal - promo.discount_amount))

        total_discount = round_money(total_discount + promo.discount_amount)
        applied.append(promo)

    total_amount = round_money(max(0, base_subtotal + shipping_amount - total_discount))
    primary = selected[0][0] if selected else None
    eligible = max((item.eligible_subtotal_amount for _, item in selected), default=0.0)

    return PromotionCalculationResult(
        subtotal_amount=round_money(base_subtotal),
        shipping_amount=round_money(shipping_amount),
        discount_amount=total_discount,
        total_amount=total_amount,
        applied_promotions=applied,
        primary_voucher=primary,
        eligible_subtotal_amount=round_money(eligible),
    )


def calculate_best_discount(
    db: Session,
    user: User,
    items: list[OrderItemCreate],
    shipping_amount: float,
    *,
    voucher_code: str | None = None,
    include_auto_apply: bool = True,
) -> PromotionCalculationResult:
    """Evaluate manual code + auto-apply promos with priority/stacking rules."""
    from app.services.pricing_service import resolve_cart_line_prices

    if not items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add at least one item before applying promotions.",
        )

    pricing = resolve_cart_line_prices(db, items)
    base_subtotal = round_money(
        sum(
            pricing[item.product_id].sale_price * item.quantity
            for item in items
            if item.product_id in pricing
        )
    )

    rejected: list[RejectedPromotion] = []
    candidates: list[tuple[Voucher, VoucherQuote]] = []
    seen_ids: set[str] = set()

    manual = _load_manual_voucher(db, voucher_code)
    if voucher_code and manual is None:
        normalized = normalize_voucher_code(voucher_code)
        rejected.append(
            RejectedPromotion(
                code=normalized,
                voucher_id=None,
                reason="That promo code wasn't found.",
                rejection_code="not_found",
            )
        )
    elif manual is not None:
        quote, failure = try_evaluate_voucher(db, user, manual, items, shipping_amount)
        if quote:
            candidates.append((manual, quote))
            seen_ids.add(str(manual.id))
        elif failure:
            rejected.append(failure)

    if include_auto_apply:
        for voucher in _load_auto_apply_candidates(db):
            if str(voucher.id) in seen_ids:
                continue
            quote, failure = try_evaluate_voucher(db, user, voucher, items, shipping_amount)
            if quote:
                candidates.append((voucher, quote))
                seen_ids.add(str(voucher.id))

    if not candidates:
        return PromotionCalculationResult(
            subtotal_amount=base_subtotal,
            shipping_amount=round_money(shipping_amount),
            discount_amount=0.0,
            total_amount=round_money(base_subtotal + shipping_amount),
            rejected_promotions=rejected,
        )

    selected = _resolve_promotion_set(candidates)
    result = _merge_quotes(base_subtotal, shipping_amount, selected)
    result.rejected_promotions = rejected
    return result


def calculate_voucher_quote(
    db: Session,
    user: User,
    voucher_code: str,
    items: list[OrderItemCreate],
    shipping_amount: float,
) -> VoucherQuote:
    """Backward-compatible single-code quote used by legacy preview paths."""
    result = calculate_best_discount(
        db,
        user,
        items,
        shipping_amount,
        voucher_code=voucher_code,
        include_auto_apply=False,
    )

    if result.primary_voucher is None:
        rejection = result.rejected_promotions[0] if result.rejected_promotions else None
        status_code = status.HTTP_404_NOT_FOUND
        if rejection and rejection.rejection_code not in {"not_found"}:
            status_code = status.HTTP_400_BAD_REQUEST
        raise HTTPException(
            status_code=status_code,
            detail=rejection.reason if rejection else "That promo code wasn't found.",
        )

    return VoucherQuote(
        voucher=result.primary_voucher,
        discount_amount=result.discount_amount,
        eligible_subtotal_amount=result.eligible_subtotal_amount,
        subtotal_amount=result.subtotal_amount,
        shipping_amount=result.shipping_amount,
        total_amount=result.total_amount,
    )


def suggest_best_promotions(
    db: Session,
    user: User,
    items: list[OrderItemCreate],
    shipping_amount: float,
    *,
    limit: int = 5,
) -> list[PromotionCalculationResult]:
    ranked: list[PromotionCalculationResult] = []
    for code in _wallet_candidate_codes(db, user):
        result = calculate_best_discount(
            db,
            user,
            items,
            shipping_amount,
            voucher_code=code,
            include_auto_apply=False,
        )
        if result.discount_amount > 0:
            ranked.append(result)

    ranked.sort(key=lambda item: item.discount_amount, reverse=True)
    return ranked[:limit]
