"""What happens to the money when one order ships from three shops.

These run against real Postgres because the guarantee under test is a
settlement write — who got credited, how much, and whether a second
confirmation can double-pay. A unit test over the roll-up functions cannot
see any of that.

The failure each one prevents was live in production before the package
split: `Order` carried a single `vendor_status`/`delivery_status` pair and
settlement ran with no vendor scope, so the first shop to move spoke for all
of them and the first confirmation paid all of them.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from tests.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def three_shop_order(db, make_user):
    """Ama's cart: a GH₵400 dress from Kofi, GH₵120 sneakers from Ben, a
    GH₵30 phone case from Esi. One order, three packages, each shop charging
    its own delivery fee."""
    from app.models import Order, OrderItem, OrderPackage, Store, VendorWallet

    customer = make_user()
    shops = []
    for name, subtotal, fee in (("Kofi", 400.0, 15.0), ("Ben", 120.0, 19.0), ("Esi", 30.0, 0.0)):
        vendor = make_user(role="vendor")
        store = Store(
            id=f"s-{uuid.uuid4().hex[:8]}",
            slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}",
            title=f"{name} Shop",
            image_key="x.jpg",
            vendor_user_id=vendor.id,
        )
        db.add(store)
        db.add(VendorWallet(vendor_user_id=vendor.id, available_balance=0.0))
        shops.append((vendor, store, subtotal, fee))
    db.flush()

    total_items = sum(s[2] for s in shops)
    total_fees = sum(s[3] for s in shops)
    order = Order(
        order_number=f"ORD-{uuid.uuid4().hex[:10].upper()}",
        user_id=customer.id,
        subtotal_amount=total_items,
        shipping_amount=total_fees,
        total_amount=total_items + total_fees,
        payment_status="paid",
        status="processing",
        address_full_name="Ama",
        address_phone="0200000000",
        address_street="1 Test Street",
        address_city="Accra",
        address_region="Greater Accra",
        payment_type="momo",
        payment_label="MTN",
    )
    db.add(order)
    db.flush()

    for index, (vendor, store, subtotal, fee) in enumerate(shops, start=1):
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=f"p{index}",
                title=f"Item {index}",
                quantity=1,
                unit_price=subtotal,
                line_total=subtotal,
                vendor_user_id=vendor.id,
                store_id=store.id,
            )
        )
        db.add(
            OrderPackage(
                order_id=order.id,
                vendor_user_id=vendor.id,
                store_id=store.id,
                store_name=store.title,
                package_number=index,
                items_subtotal=subtotal,
                delivery_fee=fee,
                vendor_status="out_for_delivery",
                delivery_status="out_for_delivery",
            )
        )
    db.flush()
    db.refresh(order)
    return order, [s[0] for s in shops], [s[3] for s in shops]


def _settlements(db, order_id):
    from app.models import VendorWalletTransaction

    return list(
        db.scalars(
            select(VendorWalletTransaction).where(
                VendorWalletTransaction.order_id == order_id,
                VendorWalletTransaction.kind == "sale_settlement",
            )
        ).all()
    )


def test_confirming_one_package_pays_only_that_vendor(db, three_shop_order):
    """The failure this prevents: Ama's dress arrives, she confirms it, and
    Ben and Esi are credited for sneakers and a phone case still sitting on
    their own shelves. Settlement used to run unscoped over the whole order."""
    from app.controllers.wallet_controller import settle_vendor_wallets_for_order

    order, vendors, _fees = three_shop_order
    kofi = vendors[0]

    settle_vendor_wallets_for_order(db, order, vendor_scope={kofi.id})
    db.flush()

    paid = _settlements(db, order.id)
    assert [row.vendor_user_id for row in paid] == [kofi.id]


def test_each_vendor_receives_their_own_delivery_fee_uncommissioned(db, three_shop_order):
    """The fee follows the rider. Kofi paid a rider GH₵15 out of pocket, so
    GH₵15 comes back to Kofi — on top of the goods, and with no commission
    taken from it, because taking a cut of a cost reimbursement would recreate
    the very problem moving the fee to the vendor was meant to solve."""
    from app.controllers.wallet_controller import settle_vendor_wallets_for_order
    from app.services.finance_math import vendor_allocation_map

    order, vendors, fees = three_shop_order
    kofi, kofi_fee = vendors[0], fees[0]

    expected_goods = vendor_allocation_map(order, vendor_scope={kofi.id})[kofi.id]

    settle_vendor_wallets_for_order(db, order, vendor_scope={kofi.id})
    db.flush()

    row = _settlements(db, order.id)[0]
    assert row.delivery_fee_amount == pytest.approx(kofi_fee)
    assert row.amount == pytest.approx(expected_goods["net_amount"] + kofi_fee)
    # Commission is charged on the goods only — never on the ride.
    assert row.commission_amount == pytest.approx(expected_goods["commission_amount"])


def test_a_shop_that_delivers_free_is_paid_no_delivery_fee(db, three_shop_order):
    """Esi charges nothing for delivery, so nothing is added to her goods."""
    from app.controllers.wallet_controller import settle_vendor_wallets_for_order

    order, vendors, fees = three_shop_order
    esi = vendors[2]
    assert fees[2] == 0.0

    settle_vendor_wallets_for_order(db, order, vendor_scope={esi.id})
    db.flush()

    row = _settlements(db, order.id)[0]
    assert row.delivery_fee_amount is None


def test_each_vendor_settles_exactly_once_across_separate_confirmations(db, three_shop_order):
    """Three packages confirmed at three different times must produce three
    settlements — one per vendor — and no vendor may be credited twice when a
    confirmation is retried."""
    from app.controllers.wallet_controller import settle_vendor_wallets_for_order

    order, vendors, _fees = three_shop_order

    for vendor in vendors:
        settle_vendor_wallets_for_order(db, order, vendor_scope={vendor.id})
        db.flush()
    # Ama taps confirm again on a flaky connection.
    for vendor in vendors:
        settle_vendor_wallets_for_order(db, order, vendor_scope={vendor.id})
        db.flush()

    paid = _settlements(db, order.id)
    assert len(paid) == 3
    assert {row.vendor_user_id for row in paid} == {v.id for v in vendors}


def test_the_wallet_balance_reflects_goods_plus_delivery(db, three_shop_order):
    from app.controllers.wallet_controller import settle_vendor_wallets_for_order
    from app.models import VendorWallet

    order, vendors, fees = three_shop_order
    kofi, kofi_fee = vendors[0], fees[0]

    settle_vendor_wallets_for_order(db, order, vendor_scope={kofi.id})
    db.flush()

    wallet = db.scalar(select(VendorWallet).where(VendorWallet.vendor_user_id == kofi.id))
    row = _settlements(db, order.id)[0]
    assert wallet.available_balance == pytest.approx(row.amount)
    assert wallet.available_balance > kofi_fee


def test_one_vendor_cancelling_leaves_the_others_payable(db, three_shop_order):
    """A shop withdrawing its items must not take the rest of the order's
    money with it — the old code refused the cancellation outright on a shared
    cart precisely because it could not express this."""
    from app.controllers.wallet_controller import settle_vendor_wallets_for_order
    from app.services.order_package_service import recompute_order_rollup

    order, vendors, _fees = three_shop_order
    esi = vendors[2]

    esi_package = next(p for p in order.packages if p.vendor_user_id == esi.id)
    esi_package.vendor_status = "cancelled"
    recompute_order_rollup(order)
    db.flush()

    for vendor in vendors[:2]:
        settle_vendor_wallets_for_order(db, order, vendor_scope={vendor.id})
    db.flush()

    paid = _settlements(db, order.id)
    assert {row.vendor_user_id for row in paid} == {vendors[0].id, vendors[1].id}
