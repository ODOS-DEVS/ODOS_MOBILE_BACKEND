"""Unit tests for stock visibility and inventory alert thresholds."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

from app.services.inventory_service import (
    LOW_STOCK_THRESHOLD,
    OUT_OF_STOCK_STATUS,
    apply_inventory_side_effects,
    mark_product_out_of_stock,
)


def _product(**overrides):
    base = {
        "id": "prod-1",
        "title": "Test Product",
        "stock": 5,
        "status": "active",
        "is_active": True,
        "vendor_user_id": uuid.uuid4(),
        "image_key": None,
        "image_url": None,
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
