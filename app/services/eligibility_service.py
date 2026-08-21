"""User eligibility evaluation for time-limited promotions and personalized campaigns."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, User

COMPLETED_ORDER_STATUSES = ("paid", "processing", "shipped", "delivered", "completed")


@dataclass(slots=True)
class EligibilityRules:
    """Targeting rules for promo eligibility evaluation."""

    min_lifetime_order_count: int | None = None
    max_lifetime_order_count: int | None = None
    min_lifetime_spend: float | None = None
    dormant_days_since_last_order: int | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> EligibilityRules | None:
        if not data:
            return None
        return cls(
            min_lifetime_order_count=data.get("min_lifetime_order_count"),
            max_lifetime_order_count=data.get("max_lifetime_order_count"),
            min_lifetime_spend=data.get("min_lifetime_spend"),
            dormant_days_since_last_order=data.get("dormant_days_since_last_order"),
        )

    def to_dict(self) -> dict:
        return {
            k: v for k, v in {
                "min_lifetime_order_count": self.min_lifetime_order_count,
                "max_lifetime_order_count": self.max_lifetime_order_count,
                "min_lifetime_spend": self.min_lifetime_spend,
                "dormant_days_since_last_order": self.dormant_days_since_last_order,
            }.items()
            if v is not None
        }


@dataclass(slots=True)
class UserEligibilityStats:
    """Computed user statistics for eligibility evaluation."""

    order_count: int
    lifetime_spend: float
    last_order_at: datetime | None


def parse_eligibility_rules(raw: dict | None) -> EligibilityRules | None:
    """Parse raw eligibility rules dict into typed dataclass."""
    return EligibilityRules.from_dict(raw)


def compute_user_eligibility_stats(db: Session, user_id: str) -> UserEligibilityStats:
    """Compute user lifetime order count, spend, and most recent order date."""
    row = db.execute(
        select(
            func.count(Order.id).label("order_count"),
            func.coalesce(func.sum(Order.total_amount), 0).label("lifetime_spend"),
            func.max(Order.paid_at).label("last_order_at"),
        ).where(
            Order.user_id == user_id,
            Order.status.in_(COMPLETED_ORDER_STATUSES),
        )
    ).one()

    return UserEligibilityStats(
        order_count=int(row.order_count or 0),
        lifetime_spend=float(row.lifetime_spend or 0.0),
        last_order_at=row.last_order_at,
    )


def evaluate_eligibility(
    rules: EligibilityRules | None,
    stats: UserEligibilityStats,
    *,
    now: datetime,
) -> tuple[bool, str | None]:
    """
    Evaluate if a user meets all eligibility rules.

    Returns (is_eligible, reason_if_ineligible).
    If is_eligible is True, reason is None.
    If is_eligible is False, reason contains the first failed rule as a user-facing message.
    """
    if rules is None:
        return True, None

    if rules.min_lifetime_order_count is not None:
        if stats.order_count < rules.min_lifetime_order_count:
            return (
                False,
                f"You need to have at least {rules.min_lifetime_order_count} order(s) to use this promotion.",
            )

    if rules.max_lifetime_order_count is not None:
        if stats.order_count > rules.max_lifetime_order_count:
            return (
                False,
                "This promotion is not available for your account.",
            )

    if rules.min_lifetime_spend is not None:
        if stats.lifetime_spend < rules.min_lifetime_spend:
            return (
                False,
                f"You need to have spent at least GH₵{rules.min_lifetime_spend:.0f} to use this promotion.",
            )

    if rules.dormant_days_since_last_order is not None:
        if stats.last_order_at is None:
            return (
                False,
                "This promotion is only for returning customers.",
            )
        days_since = (now - stats.last_order_at).days
        if days_since < rules.dormant_days_since_last_order:
            return (
                False,
                "This promotion is only available to customers we haven't seen in a while.",
            )

    return True, None
