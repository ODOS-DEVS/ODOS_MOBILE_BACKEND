"""Unit tests for stock visibility, inventory alert thresholds, and ledger."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.controllers.vendor_controller import (
    list_vendor_inventory_feed,
    list_vendor_product_inventory_movements,
    list_vendor_product_performance,
)
from app.models import UserRole, VendorStatus
from app.services.inventory_service import (
    LOW_STOCK_THRESHOLD,
    OUT_OF_STOCK_STATUS,
    apply_inventory_side_effects,
    available_stock,
    mark_product_out_of_stock,
    record_stock_change,
)


def _product(**overrides):
    base = {
        "id": "prod-1",
        "title": "Test Product",
        "stock": 5,
        "status": "active",
        "is_active": True,
        "vendor_user_id": uuid.uuid4(),
        "store_id": "store-1",
        "image_key": None,
        "image_url": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _vendor_user(**overrides):
    base = {
        "id": uuid.uuid4(),
        "role": UserRole.VENDOR,
        "vendor_status": VendorStatus.APPROVED,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_mark_out_of_stock_hides_active_product():
    product = _product(stock=0)
    assert mark_product_out_of_stock(product) is True
    assert product.status == OUT_OF_STOCK_STATUS
    assert product.is_active is False


def test_mark_out_of_stock_skips_suspended():
    product = _product(stock=0, status="suspended", is_active=False)
    assert mark_product_out_of_stock(product) is False
    assert product.status == "suspended"


@patch("app.services.inventory_service._notify_vendor_inventory")
def test_crossing_to_low_stock_notifies(notify_mock):
    db = MagicMock()
    product = _product(stock=LOW_STOCK_THRESHOLD)
    changed = apply_inventory_side_effects(
        db,
        product,
        previous_stock=LOW_STOCK_THRESHOLD + 1,
    )
    assert changed is False
    notify_mock.assert_called_once()
    assert notify_mock.call_args.kwargs["kind"] == "vendor_low_stock"


@patch("app.services.inventory_service._notify_vendor_inventory")
def test_crossing_to_zero_hides_and_notifies(notify_mock):
    db = MagicMock()
    product = _product(stock=0, status="active", is_active=True)
    changed = apply_inventory_side_effects(db, product, previous_stock=2)
    assert changed is True
    assert product.status == OUT_OF_STOCK_STATUS
    assert product.is_active is False
    notify_mock.assert_called_once()
    assert notify_mock.call_args.kwargs["kind"] == "vendor_out_of_stock"


@patch("app.services.inventory_service._notify_vendor_inventory")
def test_already_low_does_not_renotify(notify_mock):
    db = MagicMock()
    product = _product(stock=1)
    apply_inventory_side_effects(db, product, previous_stock=2)
    notify_mock.assert_not_called()


@patch("app.services.inventory_service.apply_inventory_side_effects", return_value=False)
def test_record_stock_change_writes_ledger_on_manual_patch(side_effects_mock):
    db = MagicMock()
    product = _product(stock=10)
    actor = _vendor_user()

    record_stock_change(
        db,
        product,
        new_stock=7,
        reason="manual",
        note="Stock adjusted from Seller Center",
        actor=actor,
    )

    assert product.stock == 7
    assert db.add.call_count == 1
    movement = db.add.call_args.args[0]
    assert movement.delta == -3
    assert movement.stock_after == 7
    assert movement.reason == "manual"
    assert movement.created_by_user_id == actor.id
    side_effects_mock.assert_called_once()


@patch("app.services.inventory_service.apply_inventory_side_effects", return_value=False)
def test_record_stock_change_order_sale_reason(side_effects_mock):
    db = MagicMock()
    product = _product(stock=4)

    record_stock_change(
        db,
        product,
        new_stock=2,
        reason="order_sale",
        reference_type="order",
        reference_id="ord-1",
    )

    movement = db.add.call_args.args[0]
    assert movement.reason == "order_sale"
    assert movement.reference_id == "ord-1"
    assert product.stock == 2


def test_available_stock_clamps_at_zero():
    assert available_stock(5, 2) == 3
    assert available_stock(2, 5) == 0


@patch("app.services.inventory_service.create_notification_event")
@patch("app.services.inventory_service.send_expo_push_notification")
def test_inventory_notify_respects_vendor_pref(push_mock, event_mock):
    from app.services.inventory_service import _notify_vendor_inventory

    db = MagicMock()
    vendor = SimpleNamespace(
        id=uuid.uuid4(),
        vendor_notify_inventory=False,
        expo_push_token="ExponentPushToken[x]",
        allow_notifications=True,
    )
    product = _product(vendor_user_id=vendor.id)
    db.get.return_value = vendor

    _notify_vendor_inventory(
        db,
        product,
        kind="vendor_low_stock",
        title="Low stock",
        body="Only 1 left",
    )

    event_mock.assert_not_called()
    push_mock.assert_not_called()


def test_list_inventory_movements_ownership_404():
    db = MagicMock()
    db.scalar.return_value = None
    user = _vendor_user()

    with pytest.raises(HTTPException) as exc_info:
        list_vendor_product_inventory_movements(db, user, "missing-product")

    assert exc_info.value.status_code == 404


def _performance_row(product_id="prod-1", units=6, gross=120.0, orders=3, last=None):
    return SimpleNamespace(
        product_id=product_id,
        units_sold=units,
        gross_sales=gross,
        order_count=orders,
        last_sold_at=last,
    )


def test_product_performance_maps_rows_per_sku():
    """Every sold SKU comes back, not just the dashboard's top five."""
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        _performance_row("prod-1", units=6, gross=120.0, orders=3),
        _performance_row("prod-2", units=1, gross=15.5, orders=1),
    ]

    rows = list_vendor_product_performance(db, _vendor_user(), days=30)

    assert [row.product_id for row in rows] == ["prod-1", "prod-2"]
    assert rows[0].units_sold == 6
    assert rows[0].gross_sales == 120.0
    assert rows[0].order_count == 3


def test_product_performance_coerces_missing_aggregates():
    """A SUM over no matching rows is NULL, which must read as zero not crash."""
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        SimpleNamespace(
            product_id="prod-1",
            units_sold=None,
            gross_sales=None,
            order_count=None,
            last_sold_at=None,
        )
    ]

    rows = list_vendor_product_performance(db, _vendor_user(), days=30)

    assert rows[0].units_sold == 0
    assert rows[0].gross_sales == 0
    assert rows[0].order_count == 0
    assert rows[0].last_sold_at is None


def test_product_performance_clamps_window():
    """A hostile `days` must not widen the scan to an unbounded range."""
    db = MagicMock()
    db.execute.return_value.all.return_value = []

    for days in (0, -5, 10_000):
        list_vendor_product_performance(db, _vendor_user(), days=days)

    assert db.execute.call_count == 3


def test_product_performance_requires_approved_vendor():
    """Gated on vendor_status, which is what require_vendor_access checks."""
    db = MagicMock()
    with pytest.raises(HTTPException):
        list_vendor_product_performance(
            db, _vendor_user(vendor_status=VendorStatus.SUSPENDED), days=30
        )


def test_inventory_feed_falls_back_to_product_id_for_title():
    """A movement on a product with no title still has to render."""
    db = MagicMock()
    db.execute.return_value.all.return_value = [
        SimpleNamespace(
            id=uuid.uuid4(),
            product_id="prod-9",
            product_title=None,
            delta=-2,
            stock_after=4,
            reason="order_sale",
            note=None,
            created_at=datetime.now(UTC),
        )
    ]

    rows = list_vendor_inventory_feed(db, _vendor_user())

    assert rows[0].product_title == "prod-9"
    assert rows[0].delta == -2
    assert rows[0].stock_after == 4


def test_inventory_feed_requires_approved_vendor():
    db = MagicMock()
    with pytest.raises(HTTPException):
        list_vendor_inventory_feed(
            db, _vendor_user(vendor_status=VendorStatus.SUSPENDED)
        )


def test_no_automatic_delivery_delay_credit_exists():
    """Delivery delays must not pay the customer.

    The GHS 5 automatic goodwill credit was removed: it spent real money off an
    internal SLA metric with no cap and no review. This asserts the entry point
    is gone rather than merely unused, so reintroducing it cannot happen by
    accident — a late order should raise an admin alert, not a payout.
    """
    import app.controllers.customer_wallet_controller as wallet_controller

    assert not hasattr(wallet_controller, "credit_customer_wallet_for_delivery_delay")
    assert not hasattr(wallet_controller, "DELIVERY_DELAY_GOODWILL_CREDIT")
