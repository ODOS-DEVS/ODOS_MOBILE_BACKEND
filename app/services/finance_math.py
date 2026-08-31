from __future__ import annotations

import uuid
from collections import defaultdict

from app.core.config import settings
from app.models import Order, OrderItem


def round_money(value: float) -> float:
    return round(float(value or 0), 2)


def amount_to_subunit(value: float) -> int:
    return int(round(float(value or 0) * 100))


def amount_from_subunit(value: int | None) -> float:
    return round_money((value or 0) / 100)



def _apportion_discount(
    eligible_by_vendor: dict[uuid.UUID, float],
    *,
    discount_pool: float,
    eligible_subtotal: float,
) -> dict[uuid.UUID, float]:
    """Split a discount across vendors so the parts sum to the whole.

    Rounding each vendor's share independently does not add up. Three vendors
    splitting GHS 10.00 each get 3.33, totalling 9.99 — so the vendors' gross
    came to 90.01 against a 90.00 order and ODOS settled a pesewa it never
    collected. Small, but on every multi-vendor discounted order, and invisible
    to any balance check because each individual record is self-consistent.

    Apportioned in integer pesewas by the largest-remainder method: floor every
    share, then hand the leftover pesewas to whoever was rounded down hardest.
    The total is exact by construction, and working in integers means the
    arithmetic carries no float error of its own.
    """
    if discount_pool <= 0 or eligible_subtotal <= 0:
        return {}

    pool_pesewas = int(round(discount_pool * 100))
    total_eligible_pesewas = int(round(eligible_subtotal * 100))
    if pool_pesewas <= 0 or total_eligible_pesewas <= 0:
        return {}

    # A discount can exceed what it applies to (a large fixed-amount voucher on
    # a small basket). Cap it so no vendor is allocated a negative gross.
    pool_pesewas = min(pool_pesewas, total_eligible_pesewas)

    vendor_pesewas = {
        vendor_id: int(round(amount * 100))
        for vendor_id, amount in eligible_by_vendor.items()
        if amount > 0
    }
    if not vendor_pesewas:
        return {}

    shares: dict[uuid.UUID, int] = {}
    remainders: list[tuple[int, uuid.UUID]] = []
    for vendor_id, amount_pesewas in vendor_pesewas.items():
        exact = amount_pesewas * pool_pesewas
        shares[vendor_id] = exact // total_eligible_pesewas
        remainders.append((exact % total_eligible_pesewas, vendor_id))

    leftover = pool_pesewas - sum(shares.values())
    # Largest remainder first; vendor id breaks ties so the result is stable
    # rather than dependent on dict ordering.
    remainders.sort(key=lambda pair: (-pair[0], str(pair[1])))
    for index in range(leftover):
        _, vendor_id = remainders[index % len(remainders)]
        shares[vendor_id] += 1

    # Never allocate a vendor more discount than they have goods for.
    for vendor_id in shares:
        shares[vendor_id] = min(shares[vendor_id], vendor_pesewas[vendor_id])

    return {vendor_id: pesewas / 100 for vendor_id, pesewas in shares.items()}


def vendor_allocation_map(
    order: Order,
    *,
    vendor_scope: set[uuid.UUID] | None = None,
    commission_rate: float | None = None,
    voucher_store_id: str | None = None,
) -> dict[uuid.UUID, dict[str, float]]:
    grouped_subtotals: dict[uuid.UUID, float] = defaultdict(float)
    for item in order.items:
        if not item.vendor_user_id:
            continue
        if vendor_scope and item.vendor_user_id not in vendor_scope:
            continue
        grouped_subtotals[item.vendor_user_id] += float(item.line_total)

    effective_commission_rate = (
        float(settings.vendor_commission_rate)
        if commission_rate is None
        else float(commission_rate)
    )

    discount_pool = float(order.discount_amount or 0)
    if voucher_store_id:
        eligible_subtotal = sum(
            float(item.line_total)
            for item in order.items
            if item.store_id == voucher_store_id
        )
    else:
        eligible_subtotal = float(order.subtotal_amount or 0)

    # How much of the discount each vendor is eligible to absorb. A
    # store-scoped voucher only touches that store's lines.
    eligible_by_vendor: dict[uuid.UUID, float] = {}
    for vendor_user_id, subtotal in grouped_subtotals.items():
        if voucher_store_id:
            eligible_by_vendor[vendor_user_id] = sum(
                float(item.line_total)
                for item in order.items
                if item.vendor_user_id == vendor_user_id
                and item.store_id == voucher_store_id
            )
        else:
            eligible_by_vendor[vendor_user_id] = subtotal

    discount_shares = _apportion_discount(
        eligible_by_vendor,
        discount_pool=discount_pool,
        eligible_subtotal=eligible_subtotal,
    )

    allocations: dict[uuid.UUID, dict[str, float]] = {}
    for vendor_user_id, subtotal in grouped_subtotals.items():
        discount_share = discount_shares.get(vendor_user_id, 0.0)
        gross_amount = max(subtotal - discount_share, 0.0)
        commission_amount = gross_amount * effective_commission_rate
        net_amount = max(gross_amount - commission_amount, 0.0)
        allocations[vendor_user_id] = {
            "subtotal": round_money(subtotal),
            "discount_share": round_money(discount_share),
            "gross_amount": round_money(gross_amount),
            "commission_amount": round_money(commission_amount),
            "net_amount": round_money(net_amount),
        }

    return allocations


def return_reversal_breakdown(
    order: Order,
    order_item: OrderItem,
    quantity: int,
    *,
    commission_rate: float | None = None,
) -> dict[str, float]:
    effective_commission_rate = (
        float(settings.vendor_commission_rate)
        if commission_rate is None
        else float(commission_rate)
    )
    line_discount_share = 0.0
    if order.subtotal_amount > 0 and order.discount_amount > 0:
        line_discount_share = (
            (float(order_item.line_total) / float(order.subtotal_amount))
            * float(order.discount_amount)
        )

    gross_line_amount = max(float(order_item.line_total) - line_discount_share, 0.0)
    quantity_ratio = quantity / max(order_item.quantity, 1)
    refund_gross_amount = round_money(gross_line_amount * quantity_ratio)
    refund_commission_amount = round_money(refund_gross_amount * effective_commission_rate)
    refund_net_amount = round_money(max(refund_gross_amount - refund_commission_amount, 0.0))
    return {
        "gross_amount": refund_gross_amount,
        "commission_amount": refund_commission_amount,
        "net_amount": refund_net_amount,
    }
