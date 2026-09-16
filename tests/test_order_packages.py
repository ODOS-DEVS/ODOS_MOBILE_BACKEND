"""Unit tests for the order-package split and per-shop delivery pricing.

Three live faults motivated the split, and the first three sections below pin
each one so it cannot come back:

  1. A shared `vendor_status` meant the second vendor to touch a multi-shop
     order was rejected by the forward-transition guard — locked out of items
     sitting on their own shelf.
  2. A shared `delivery_status` meant the first vendor to dispatch flipped the
     whole order to out_for_delivery and started one auto-release clock for
     every shop.
  3. Unscoped settlement meant confirming the one package that arrived paid
     every vendor on the order.

The fourth section covers the money rule that came with it: the delivery fee
is charged per package and follows the rider.

These are pure-function tests over the roll-up and pricing logic. Stateful
flows (dispatch -> confirm -> settle across two vendors) need a live session
and are exercised against a real Postgres instance, mirroring this suite's
existing convention — see the note at the top of test_delivery_lifecycle.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.controllers.vendor_controller import VENDOR_STATUS_FORWARD_TRANSITIONS
from app.services.delivery_service import DEFAULT_DELIVERY_CONFIG as CONFIG
from app.services.order_package_service import (
    recompute_order_rollup,
    rollup_delivery_status,
    rollup_settlement_status,
    rollup_vendor_status,
)
from app.services.package_pricing_service import (
    PackageGroup,
    build_package_delivery_options,
    packages_shipping_total,
    quote_package,
    quote_packages,
    store_delivery_badge,
    vendor_delivery_pricing,
)


def _package(vendor_status, delivery_status="not_dispatched", settlement_status="not_eligible"):
    return SimpleNamespace(
        vendor_status=vendor_status,
        delivery_status=delivery_status,
        settlement_status=settlement_status,
        dispatched_at=None,
        dispatch_attempt_count=0,
        delivered_at=None,
        cancelled_at=None,
        auto_release_at=None,
        delivery_problem_reason=None,
        delivery_problem_reported_at=None,
    )


def _store(**overrides):
    base = {
        "delivery_fee_economy": None,
        "delivery_fee_express": None,
        "delivery_fee_same_day": None,
        "free_delivery_threshold": None,
        "express_delivery_enabled": True,
        "same_day_delivery_enabled": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _group(store, subtotal, name="Shop"):
    return PackageGroup(
        vendor_user_id=name,
        store_id=name.lower(),
        store_name=name,
        store=store,
        items_subtotal=subtotal,
    )


# --- 1. One vendor can no longer lock another out -------------------------


def test_each_package_advances_against_its_own_stage():
    """The guard compares against the package, so a fast shop reaching
    "ready" cannot push a slow shop's next legal step out of reach."""
    fast = _package("ready")
    slow = _package("pending")

    assert VENDOR_STATUS_FORWARD_TRANSITIONS[slow.vendor_status] == "confirmed"
    assert VENDOR_STATUS_FORWARD_TRANSITIONS[fast.vendor_status] == "out_for_delivery"


def test_order_stage_is_the_least_advanced_package():
    packages = [_package("ready"), _package("processing")]
    assert rollup_vendor_status(packages) == "processing"


def test_order_is_only_delivered_when_every_package_is():
    assert rollup_vendor_status([_package("delivered"), _package("out_for_delivery")]) == (
        "out_for_delivery"
    )
    assert rollup_vendor_status([_package("delivered"), _package("delivered")]) == "delivered"


def test_a_cancelled_package_does_not_speak_for_the_order():
    packages = [_package("cancelled"), _package("ready")]
    assert rollup_vendor_status(packages) == "ready"


def test_order_is_cancelled_only_when_every_package_is():
    assert rollup_vendor_status([_package("cancelled"), _package("cancelled")]) == "cancelled"


# --- 2. One dispatch no longer ships the whole order ----------------------


def test_one_dispatched_package_does_not_make_the_order_delivered():
    packages = [
        _package("out_for_delivery", "out_for_delivery"),
        _package("processing"),
    ]
    # Something is genuinely on the road, so the delivery headline says so...
    assert rollup_delivery_status(packages) == "out_for_delivery"
    # ...but the order's fulfilment stage still reports the shop still packing.
    assert rollup_vendor_status(packages) == "processing"


def test_a_problem_on_one_package_surfaces_above_a_delivered_one():
    """A summary that hides the one bag that went wrong is worse than none."""
    packages = [
        _package("out_for_delivery", "customer_problem"),
        _package("delivered", "delivered"),
    ]
    assert rollup_delivery_status(packages) == "customer_problem"


def test_progress_reflects_how_many_packages_have_moved():
    order = SimpleNamespace(
        packages=[_package("out_for_delivery", "out_for_delivery"), _package("processing")],
        status="processing",
        progress=None,
        vendor_status=None,
        delivery_status=None,
        settlement_status=None,
        dispatched_at=None,
        dispatch_attempt_count=0,
        delivered_at=None,
        cancelled_at=None,
        auto_release_at=None,
        delivery_problem_reason=None,
        delivery_problem_reported_at=None,
    )
    recompute_order_rollup(order)
    # Halfway between "out for delivery" (0.9) and "processing" (0.45).
    assert order.progress == pytest.approx(0.68, abs=0.01)
    assert order.vendor_status == "processing"
    assert order.delivery_status == "out_for_delivery"


# --- 3. Settlement follows the package, not the order ---------------------


def test_settlement_is_not_complete_until_every_package_settles():
    packages = [
        _package("delivered", "delivered", "settled"),
        _package("out_for_delivery", "out_for_delivery", "eligible"),
    ]
    assert rollup_settlement_status(packages) == "eligible"


def test_a_held_package_holds_the_order_summary():
    packages = [
        _package("delivered", "delivered", "settled"),
        _package("out_for_delivery", "customer_problem", "held"),
    ]
    assert rollup_settlement_status(packages) == "held"


def test_order_settles_only_when_all_live_packages_have():
    packages = [
        _package("delivered", "delivered", "settled"),
        _package("delivered", "delivered", "settled"),
    ]
    assert rollup_settlement_status(packages) == "settled"


# --- 4. The delivery fee follows the rider --------------------------------


def test_a_shop_that_sets_nothing_prices_exactly_as_the_platform_does():
    pricing = vendor_delivery_pricing(_store(), CONFIG)
    assert pricing.economy_fee == CONFIG.economy_fee
    assert pricing.free_threshold == CONFIG.free_shipping_threshold
    assert pricing.is_custom is False


def test_a_shop_can_undercut_the_platform_fee():
    pricing = vendor_delivery_pricing(_store(delivery_fee_economy=8.0), CONFIG)
    assert pricing.economy_fee == 8.0
    assert pricing.is_custom is True


def test_zero_is_a_real_price_not_a_missing_one():
    """A shop charging nothing must not silently fall back to GH₵19."""
    pricing = vendor_delivery_pricing(_store(delivery_fee_economy=0.0), CONFIG)
    assert pricing.economy_fee == 0.0


def test_each_shop_is_charged_and_waived_on_its_own_basket():
    groups = [
        _group(_store(free_delivery_threshold=250.0), 400.0, "Kofi"),  # over its own bar
        _group(_store(), 120.0, "Ben"),                                # under the platform bar
        _group(_store(free_delivery_threshold=0.0), 30.0, "Esi"),      # always free
    ]
    kofi, ben, esi = quote_packages(groups, "economy", CONFIG)

    assert (kofi.delivery_fee, kofi.fee_waived) == (0.0, True)
    assert ben.delivery_fee == CONFIG.economy_fee
    assert (esi.delivery_fee, esi.fee_waived) == (0.0, False)
    assert packages_shipping_total([kofi, ben, esi]) == CONFIG.economy_fee


def test_the_order_total_is_the_sum_of_its_packages():
    """Three shops means three riders and three real journeys — charging once
    would mean two vendors quietly covering the difference."""
    groups = [_group(_store(), 50.0, f"Shop{i}") for i in range(3)]
    quotes = quote_packages(groups, "economy", CONFIG)
    assert packages_shipping_total(quotes) == pytest.approx(CONFIG.economy_fee * 3)


def test_no_volume_discount_for_extra_packages():
    """Every cedi of such a discount would come out of a vendor still making
    the whole journey. It becomes honest only when ODOS batches the drops."""
    one = packages_shipping_total(quote_packages([_group(_store(), 50.0)], "economy", CONFIG))
    three = packages_shipping_total(
        quote_packages([_group(_store(), 50.0, f"S{i}") for i in range(3)], "economy", CONFIG)
    )
    assert three == pytest.approx(one * 3)


def test_a_customer_is_told_how_much_more_earns_free_delivery():
    quote = quote_package(_group(_store(free_delivery_threshold=200.0), 120.0), "economy", CONFIG)
    assert quote.amount_to_free_delivery == pytest.approx(80.0)


def test_nothing_left_to_gain_once_delivery_is_already_free():
    quote = quote_package(_group(_store(free_delivery_threshold=100.0), 120.0), "economy", CONFIG)
    assert quote.amount_to_free_delivery is None


def test_badges_only_advertise_something_worth_advertising():
    assert store_delivery_badge(_store(free_delivery_threshold=0.0), CONFIG) == "Free delivery"
    assert store_delivery_badge(_store(delivery_fee_economy=0.0), CONFIG) == "Free delivery"
    assert store_delivery_badge(_store(free_delivery_threshold=150.0), CONFIG) == "Free over GH₵150"


def test_a_method_one_shop_cannot_do_is_offered_to_none_of_the_cart():
    groups = [
        _group(_store(), 100.0, "Kofi"),
        _group(_store(express_delivery_enabled=False), 100.0, "Ben"),
    ]
    options = build_package_delivery_options(
        groups=groups, region="Greater Accra", city="Accra", config=CONFIG
    )
    express = next(option for option in options if option.id == "express")
    assert express.available is False
    assert express.unavailable_reason


def test_a_single_shop_cart_prices_exactly_as_it_did_before():
    options = build_package_delivery_options(
        groups=[_group(_store(), 120.0)], region="Greater Accra", city="Accra", config=CONFIG
    )
    economy = next(option for option in options if option.id == "economy")
    assert economy.amount == CONFIG.economy_fee
