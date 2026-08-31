"""Arithmetic tests for vendor allocation.

The uniqueness constraints elsewhere prove a vendor cannot be paid *twice*.
Nothing proved they are paid the *right amount*. These cover that, and in
particular the invariant that matters commercially:

    sum(vendor gross) == what the customer actually paid for goods

If that fails, ODOS is settling more (or less) than it collected, and the gap
does not appear in any balance check because every individual record is
internally consistent.
"""

from __future__ import annotations

import types
import uuid

import pytest

from app.services.finance_math import round_money, vendor_allocation_map


def build_order(lines, *, discount: float = 0.0, subtotal: float | None = None):
    """lines: (vendor_id, line_total, store_id)"""
    items = [
        types.SimpleNamespace(vendor_user_id=vid, line_total=total, store_id=store)
        for vid, total, store in lines
    ]
    return types.SimpleNamespace(
        items=items,
        discount_amount=discount,
        subtotal_amount=subtotal if subtotal is not None else sum(l[1] for l in lines),
    )


# --- the invariant that protects the platform's money ---


def test_gross_across_vendors_equals_what_the_customer_paid():
    """Three vendors, an uneven split, a GHS 10 discount on a GHS 100 order.

    The customer pays 90.00 for goods. If the vendors' gross sums to 90.01,
    ODOS settles a pesewa it never collected — on every such order.
    """
    v1, v2, v3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    order = build_order(
        [(v1, 33.33, "s1"), (v2, 33.33, "s2"), (v3, 33.34, "s3")],
        discount=10.0,
    )

    allocations = vendor_allocation_map(order, commission_rate=0.10)
    total_gross = round_money(sum(a["gross_amount"] for a in allocations.values()))

    assert total_gross == 90.00


def test_discount_shares_sum_to_the_discount_pool():
    """Rounding each share independently loses the remainder. The shares must
    reconcile to the pool exactly, or the difference silently becomes ODOS's."""
    v1, v2, v3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    order = build_order(
        [(v1, 33.33, "s1"), (v2, 33.33, "s2"), (v3, 33.34, "s3")],
        discount=10.0,
    )

    allocations = vendor_allocation_map(order, commission_rate=0.10)
    total_share = round_money(sum(a["discount_share"] for a in allocations.values()))

    assert total_share == 10.00


@pytest.mark.parametrize(
    "line_totals,discount",
    [
        ([10.0, 10.0, 10.0], 1.0),      # thirds of a pesewa-odd discount
        ([33.33, 33.33, 33.34], 10.0),  # uneven lines
        ([0.01, 0.01, 0.01], 0.02),     # discount larger than most lines
        ([99.99, 0.01], 5.0),           # extreme imbalance
        ([7.77, 3.33, 1.11, 2.22], 4.44),
    ],
)
def test_apportionment_never_creates_or_destroys_money(line_totals, discount):
    vendors = [uuid.uuid4() for _ in line_totals]
    order = build_order(
        [(v, t, f"s{i}") for i, (v, t) in enumerate(zip(vendors, line_totals))],
        discount=discount,
    )

    allocations = vendor_allocation_map(order, commission_rate=0.10)
    subtotal = round_money(sum(line_totals))
    total_gross = round_money(sum(a["gross_amount"] for a in allocations.values()))

    assert total_gross == round_money(subtotal - discount)


# --- per-vendor consistency ---


def test_net_plus_commission_equals_gross_for_each_vendor():
    """A vendor's own record must add up, or their wallet cannot be explained
    from the settlement that produced it."""
    vendors = [uuid.uuid4() for _ in range(3)]
    order = build_order(
        [(v, t, f"s{i}") for i, (v, t) in enumerate(zip(vendors, [10.05, 20.07, 33.33]))],
        discount=3.33,
    )

    for allocation in vendor_allocation_map(order, commission_rate=0.10).values():
        assert round_money(
            allocation["net_amount"] + allocation["commission_amount"]
        ) == allocation["gross_amount"]


def test_no_discount_leaves_gross_equal_to_subtotal():
    v1 = uuid.uuid4()
    order = build_order([(v1, 10.05, "s1")])
    allocation = vendor_allocation_map(order, commission_rate=0.10)[v1]

    assert allocation["gross_amount"] == 10.05
    assert allocation["discount_share"] == 0.0


def test_a_vendor_is_never_allocated_a_negative_amount():
    """A discount larger than a vendor's line must floor at zero rather than
    turning into a debt the vendor did not incur."""
    v1 = uuid.uuid4()
    order = build_order([(v1, 5.0, "s1")], discount=20.0, subtotal=5.0)
    allocation = vendor_allocation_map(order, commission_rate=0.10)[v1]

    assert allocation["gross_amount"] >= 0.0
    assert allocation["net_amount"] >= 0.0
