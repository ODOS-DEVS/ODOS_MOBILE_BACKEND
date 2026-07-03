from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.event_types import PROMO_APPLIED, PROMO_REJECTED
from app.models import Order, User, Voucher
from app.services.event_log_service import record_admin_event, record_user_event
from app.services.promotion_engine import PromotionCalculationResult, RejectedPromotion


def log_promo_applied(
    db: Session,
    *,
    user: User,
    order: Order | None,
    result: PromotionCalculationResult,
) -> None:
    if not result.applied_promotions:
        return

    record_user_event(
        db,
        user_id=str(user.id),
        event_type=PROMO_APPLIED,
        action="promo.applied",
        entity_type="order" if order else "cart",
        entity_id=str(order.id) if order else None,
        metadata={
            "discount_amount": result.discount_amount,
            "applied_promotions": result.to_breakdown_json().get("applied_promotions", []),
            "voucher_code": result.primary_voucher.code if result.primary_voucher else None,
        },
    )


def log_promo_rejections(
    db: Session,
    *,
    user: User,
    rejections: list[RejectedPromotion],
    voucher_code: str | None = None,
) -> None:
    for rejection in rejections:
        if voucher_code and rejection.code and rejection.code != voucher_code.strip().upper():
            continue
        record_user_event(
            db,
            user_id=str(user.id),
            event_type=PROMO_REJECTED,
            action="promo.rejected",
            entity_type="voucher",
            entity_id=rejection.voucher_id,
            metadata={
                "code": rejection.code,
                "reason": rejection.reason,
                "rejection_code": rejection.rejection_code,
            },
        )


def log_admin_promo_mutation(
    db: Session,
    *,
    admin_user: User,
    event_type: str,
    action: str,
    voucher: Voucher,
    before_state: dict | None = None,
    after_state: dict | None = None,
) -> None:
    record_admin_event(
        db,
        admin_user=admin_user,
        event_type=event_type,
        action=action,
        entity_type="promotion",
        entity_id=str(voucher.id),
        before_state=before_state,
        after_state=after_state,
        metadata={"code": voucher.code, "title": voucher.title},
    )
