"""Unit tests for the marketplace voucher validation and discount engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException

from app.schemas.order import OrderItemCreate
from app.services.promotion_service import (
    discount_for_voucher,
    eligible_subtotal_for_voucher,
    validate_voucher_configuration,
    voucher_status,
    _line_is_eligible,
)


def _voucher(**overrides):
    base = {
        "id": uuid.uuid4(),
        "code": "SAVE10",
        "title": "Save 10",
        "scope": "odos",
        "owner_type": "platform",
        "store_id": None,
        "eligible_store_ids": None,
        "discount_type": "percent",
        "discount_value": 10,
        "min_subtotal": 0,
        "max_discount": None,
        "usage_limit": None,
        "per_user_limit": 1,
        "is_active": True,
        "starts_at": None,
        "ends_at": None,
        "approval_status": "approved",
        "availability": "auto",
        "promotion_type": "coupon",
        "first_order_only": False,
        "new_user_only": False,
        "category_slugs": None,
        "excluded_category_slugs": None,
        "product_ids": None,
        "excluded_product_ids": None,
        "bogo_buy_quantity": None,
        "bogo_get_quantity": None,
        "bogo_get_discount_percent": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _item(product_id: str, unit_price: float, quantity: int = 1) -> OrderItemCreate:
    return OrderItemCreate(
        product_id=product_id,
        title=f"Item {product_id}",
        quantity=quantity,
        unit_price=unit_price,
    )


def test_voucher_status_expired():
    now = datetime.now(timezone.utc)
    voucher = _voucher(ends_at=now - timedelta(hours=1))
    assert voucher_status(voucher, now=now, overall_count=0) == "expired"


def test_voucher_status_inactive():
    now = datetime.now(timezone.utc)
    voucher = _voucher(is_active=False)
    assert voucher_status(voucher, now=now, overall_count=0) == "disabled"


def test_voucher_status_usage_limit():
    now = datetime.now(timezone.utc)
    voucher = _voucher(usage_limit=2)
    assert voucher_status(voucher, now=now, overall_count=2) == "limit_reached"


def test_percent_discount_with_cap():
    voucher = _voucher(discount_type="percent", discount_value=50, max_discount=20)
    amount = discount_for_voucher(voucher, eligible_subtotal=100, shipping_amount=15)
    assert amount == 20


def test_fixed_discount():
    voucher = _voucher(discount_type="fixed", discount_value=15)
    amount = discount_for_voucher(voucher, eligible_subtotal=100, shipping_amount=0)
    assert amount == 15


def test_free_shipping_discount():
    voucher = _voucher(discount_type="free_shipping", discount_value=0)
    amount = discount_for_voucher(voucher, eligible_subtotal=100, shipping_amount=18.5)
    assert amount == 18.5


def test_vendor_voucher_rejects_other_store_items():
    voucher = _voucher(
        scope="store",
        owner_type="vendor",
        store_id="store-a",
        discount_type="percent",
        discount_value=10,
    )
    item = _item("p1", 50)
    assert (
        _line_is_eligible(
            voucher,
            item,
            product_meta={"store_id": "store-b", "category_slugs": [], "category": None},
            unit_price=50,
        )
        is False
    )
    assert (
        _line_is_eligible(
            voucher,
            item,
            product_meta={"store_id": "store-a", "category_slugs": [], "category": None},
            unit_price=50,
        )
        is True
    )


def test_eligible_store_ids_filter():
    voucher = _voucher(scope="odos", eligible_store_ids=["store-a", "store-c"])
    item = _item("p1", 40)
    assert (
        _line_is_eligible(
            voucher,
            item,
            product_meta={"store_id": "store-b", "category_slugs": [], "category": None},
            unit_price=40,
        )
        is False
    )
    assert (
        _line_is_eligible(
            voucher,
            item,
            product_meta={"store_id": "store-a", "category_slugs": [], "category": None},
            unit_price=40,
        )
        is True
    )


def test_excluded_category_blocks_line():
    voucher = _voucher(excluded_category_slugs=["electronics"])
    item = _item("p1", 40)
    assert (
        _line_is_eligible(
            voucher,
            item,
            product_meta={
                "store_id": "store-a",
                "category_slugs": ["electronics"],
                "category": "Electronics",
            },
            unit_price=40,
        )
        is False
    )


def test_eligible_subtotal_respects_product_scope():
    voucher = _voucher(scope="product", product_ids=["p1"])
    items = [_item("p1", 10, quantity=2), _item("p2", 50)]
    meta = {
        "p1": {"store_id": "s1", "category_slugs": [], "category": None},
        "p2": {"store_id": "s1", "category_slugs": [], "category": None},
    }
    prices = {"p1": 10.0, "p2": 50.0}
    total = eligible_subtotal_for_voucher(
        voucher,
        items,
        product_meta_map=meta,
        line_prices=prices,
    )
    assert total == 20.0


def test_vendor_config_requires_store_scope():
    with pytest.raises(HTTPException) as exc:
        validate_voucher_configuration(
            scope="odos",
            availability="auto",
            discount_type="percent",
            discount_value=10,
            starts_at=None,
            ends_at=None,
            usage_limit=None,
            per_user_limit=1,
            store_id=None,
            owner_type="vendor",
        )
    assert exc.value.status_code == 400


def test_vendor_cannot_target_other_stores():
    with pytest.raises(HTTPException):
        validate_voucher_configuration(
            scope="store",
            availability="auto",
            discount_type="percent",
            discount_value=10,
            starts_at=None,
            ends_at=None,
            usage_limit=None,
            per_user_limit=1,
            store_id="store-a",
            owner_type="vendor",
            eligible_store_ids=["store-b"],
        )


def test_configuration_rejects_bad_date_window():
    now = datetime.now(timezone.utc)
    with pytest.raises(HTTPException):
        validate_voucher_configuration(
            scope="odos",
            availability="auto",
            discount_type="percent",
            discount_value=10,
            starts_at=now,
            ends_at=now - timedelta(days=1),
            usage_limit=None,
            per_user_limit=None,
            store_id=None,
            owner_type="platform",
        )
