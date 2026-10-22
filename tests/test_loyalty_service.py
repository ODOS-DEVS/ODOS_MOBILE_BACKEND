"""Loyalty economics.

Points convert to wallet cash at a fixed rate, which makes this an accounting
system rather than a gamification toy. The invariants that matter:

    * an order pays out exactly once, however it was completed
    * nothing accrues on the delivery fee
    * refunded money does not leave its points behind
    * a redemption that burns points always credits the wallet
"""

from __future__ import annotations

import types
import uuid
from unittest.mock import MagicMock

import pytest

from app.services.loyalty_service import (
    LOYALTY_TIERS,
    MIN_REDEEMABLE_POINTS,
    POINTS_PER_GHS_REDEEMED,
    _order_goods_subtotal,
    award_order_loyalty_points,
    calculate_tier,
    reverse_order_loyalty_points,
)


def _package(delivery_status="delivered", vendor_status="delivered"):
    return types.SimpleNamespace(
        delivery_status=delivery_status,
        vendor_status=vendor_status,
    )


def _order(subtotal=100.0, discount=0.0, packages=None, items=None):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        order_number="ODR-1",
        subtotal_amount=subtotal,
        discount_amount=discount,
        total_amount=subtotal + 25.0,  # a delivery fee rides on top
        packages=packages if packages is not None else [_package()],
        items=items or [],
    )


def _account(points=0, spend=0.0, tier="bronze"):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        total_points=points,
        lifetime_spend=spend,
        tier_level=tier,
        tier_upgraded_at=None,
    )


def test_goods_subtotal_excludes_delivery_fee():
    """Points accrue on goods, never on the rider's fee."""
    order = _order(subtotal=100.0)
    assert order.total_amount == 125.0
    assert _order_goods_subtotal(order) == pytest.approx(100.0)


def test_goods_subtotal_nets_off_discount():
    order = _order(subtotal=100.0, discount=30.0)
    assert _order_goods_subtotal(order) == pytest.approx(70.0)


def test_goods_subtotal_falls_back_to_line_items():
    """Older orders have no subtotal_amount and must not pay out zero."""
    order = _order(subtotal=0.0, items=[types.SimpleNamespace(line_total=40.0)])
    assert _order_goods_subtotal(order) == pytest.approx(40.0)


def test_no_points_while_a_package_is_outstanding():
    """A multi-shop order pays once on the last bag, not once per shop."""
    db = MagicMock()
    order = _order(packages=[_package(), _package(delivery_status="out_for_delivery")])

    assert award_order_loyalty_points(db, order) == 0


def test_points_are_not_awarded_twice_for_one_order():
    """An existing earn row short-circuits a replayed completion."""
    db = MagicMock()
    db.scalar.return_value = uuid.uuid4()  # has_earned_points_for_order -> True
    order = _order()

    assert award_order_loyalty_points(db, order) == 0


def test_bronze_earns_one_point_per_cedi_of_goods():
    db = MagicMock()
    account = _account()
    # First scalar: the idempotency probe (None). Second: the account.
    db.scalar.side_effect = [None, account]
    order = _order(subtotal=250.0)

    earned = award_order_loyalty_points(db, order)

    assert earned == 250
    assert account.total_points == 250
    assert account.lifetime_spend == pytest.approx(250.0)


def test_gold_multiplier_applies():
    db = MagicMock()
    account = _account(tier="gold")
    db.scalar.side_effect = [None, account]

    earned = award_order_loyalty_points(db, _order(subtotal=100.0))

    assert earned == int(100 * LOYALTY_TIERS["gold"]["points_multiplier"])


def test_crossing_the_threshold_upgrades_the_tier():
    db = MagicMock()
    account = _account(spend=900.0)
    db.scalar.side_effect = [None, account]

    award_order_loyalty_points(db, _order(subtotal=200.0))

    assert account.tier_level == "silver"
    assert account.tier_upgraded_at is not None


def test_zero_value_order_earns_nothing():
    db = MagicMock()
    db.scalar.side_effect = [None, _account()]

    assert award_order_loyalty_points(db, _order(subtotal=0.0)) == 0


def test_calculate_tier_boundaries():
    assert calculate_tier(0) == "bronze"
    assert calculate_tier(999) == "bronze"
    assert calculate_tier(1000) == "silver"
    assert calculate_tier(4999) == "silver"
    assert calculate_tier(5000) == "gold"


def test_full_refund_reverses_all_points():
    """Buy, earn, return, keep the rewards — closed."""
    order = _order(subtotal=200.0)
    account = _account(points=200, spend=200.0)
    earn_row = types.SimpleNamespace(points_amount=200, account_id=account.id)

    db = MagicMock()
    db.scalar.return_value = earn_row
    db.get.return_value = account

    reversed_points = reverse_order_loyalty_points(db, order, 200.0)

    assert reversed_points == 200
    assert account.total_points == 0


def test_partial_refund_reverses_proportionally():
    order = _order(subtotal=200.0)
    account = _account(points=200, spend=200.0)
    earn_row = types.SimpleNamespace(points_amount=200, account_id=account.id)

    db = MagicMock()
    db.scalar.return_value = earn_row
    db.get.return_value = account

    assert reverse_order_loyalty_points(db, order, 50.0) == 50
    assert account.total_points == 150


def test_reversal_never_drives_the_balance_negative():
    """The points may already be spent; a debt no one can see is worse."""
    order = _order(subtotal=200.0)
    account = _account(points=30, spend=200.0)  # already redeemed most
    earn_row = types.SimpleNamespace(points_amount=200, account_id=account.id)

    db = MagicMock()
    db.scalar.return_value = earn_row
    db.get.return_value = account

    assert reverse_order_loyalty_points(db, order, 200.0) == 30
    assert account.total_points == 0


def test_reversal_without_an_earn_row_is_a_noop():
    db = MagicMock()
    db.scalar.return_value = None

    assert reverse_order_loyalty_points(db, _order(), 50.0) == 0


def test_redemption_rate_is_one_cedi_per_hundred_points():
    assert POINTS_PER_GHS_REDEEMED == 100
    assert MIN_REDEEMABLE_POINTS == 100


def test_late_orders_notify_without_paying():
    """A delay must inform the customer, never credit them.

    Guards the boundary between the two halves of this change: the GHS 5
    goodwill credit was removed, but the customer-facing apology it used to
    carry was kept. A future edit that reintroduces a payout here should fail
    this, and one that drops the notice should fail the assertions above it.
    """
    import inspect

    from app.services import delivery_ops_monitor_service as monitor

    source = inspect.getsource(monitor)

    # The notice survives.
    assert "_notify_customer_running_late" in source
    assert monitor.CUSTOMER_LATE_KIND == "order_running_late"

    # The payout does not.
    assert "credit_customer_wallet_for_delivery_delay" not in source
    assert "available_balance" not in source
