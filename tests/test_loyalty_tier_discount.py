"""The loyalty tier discount: advertised since launch, applied here.

These pin the three properties that make it safe to turn on:
it is opt-in (a business switch, not a code default), it is capped,
and it is platform-funded -- the settlement tests in test_finance_math
prove the vendor side; these prove the engine side.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.promotion_engine import (
    AppliedPromotion,
    PromotionCalculationResult,
    _apply_loyalty_tier_discount,
)


def result_with(subtotal: float, shipping: float = 0.0, applied=None):
    applied = applied or []
    discount = sum(p.discount_amount for p in applied)
    return PromotionCalculationResult(
        subtotal_amount=subtotal,
        shipping_amount=shipping,
        discount_amount=discount,
        total_amount=max(0.0, subtotal + shipping - discount),
        applied_promotions=list(applied),
    )


def voucher_line(amount: float, discount_type: str = "percent") -> AppliedPromotion:
    return AppliedPromotion(
        voucher_id="v1",
        code="SAVE",
        title="Save",
        promotion_type="coupon",
        discount_type=discount_type,
        discount_amount=amount,
        priority=0,
        stackable=True,
    )


@pytest.fixture
def tier(monkeypatch):
    """Enable the switch and put the user on a chosen tier."""

    def configure(level: str, *, enabled: bool = True, cap: float = 50.0):
        monkeypatch.setattr(settings, "loyalty_tier_discounts_enabled", enabled)
        monkeypatch.setattr(settings, "loyalty_tier_discount_cap", cap)
        import app.services.loyalty_service as loyalty

        monkeypatch.setattr(
            loyalty,
            "get_or_create_loyalty_account",
            lambda db, user: SimpleNamespace(tier_level=level),
        )

    return configure


def test_off_by_default_is_a_business_switch(tier):
    tier("gold", enabled=False)
    result = result_with(200.0)
    _apply_loyalty_tier_discount(None, None, result)
    assert result.applied_promotions == []
    assert result.discount_amount == 0.0


def test_bronze_gets_nothing(tier):
    tier("bronze")
    result = result_with(200.0)
    _apply_loyalty_tier_discount(None, None, result)
    assert result.applied_promotions == []


def test_silver_takes_five_percent_platform_funded(tier):
    tier("silver")
    result = result_with(200.0)
    _apply_loyalty_tier_discount(None, None, result)
    (line,) = result.applied_promotions
    assert line.discount_amount == 10.0
    assert line.funding == "platform"
    assert line.promotion_type == "loyalty_tier"
    assert result.discount_amount == 10.0
    assert result.total_amount == 190.0


def test_gold_is_capped_per_order(tier):
    tier("gold", cap=50.0)
    result = result_with(10_000.0)
    _apply_loyalty_tier_discount(None, None, result)
    (line,) = result.applied_promotions
    assert line.discount_amount == 50.0


def test_tier_applies_to_what_vouchers_left(tier):
    """A voucher and the tier discount never discount the same cedi twice."""
    tier("silver")
    result = result_with(100.0, applied=[voucher_line(20.0)])
    _apply_loyalty_tier_discount(None, None, result)
    loyalty_line = result.applied_promotions[-1]
    assert loyalty_line.discount_amount == 4.0  # 5% of the remaining 80
    assert result.discount_amount == 24.0
    assert result.total_amount == 76.0


def test_free_shipping_lines_do_not_shrink_the_tier_base(tier):
    """Free shipping discounts shipping, not goods; the tier base is goods."""
    tier("silver")
    result = result_with(100.0, shipping=15.0, applied=[voucher_line(15.0, discount_type="free_shipping")])
    _apply_loyalty_tier_discount(None, None, result)
    loyalty_line = result.applied_promotions[-1]
    assert loyalty_line.discount_amount == 5.0  # 5% of the full 100 of goods


def test_breakdown_json_records_the_funding_side(tier):
    tier("gold")
    result = result_with(100.0)
    _apply_loyalty_tier_discount(None, None, result)
    recorded = result.to_breakdown_json()["applied_promotions"][-1]
    assert recorded["funding"] == "platform"
    assert recorded["promotion_type"] == "loyalty_tier"
