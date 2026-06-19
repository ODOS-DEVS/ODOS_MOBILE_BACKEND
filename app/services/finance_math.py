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

    allocations: dict[uuid.UUID, dict[str, float]] = {}
    for vendor_user_id, subtotal in grouped_subtotals.items():
        discount_share = 0.0
        if discount_pool > 0 and eligible_subtotal > 0:
            if voucher_store_id:
                store_lines = [
                    item
                    for item in order.items
                    if item.vendor_user_id == vendor_user_id and item.store_id == voucher_store_id
                ]
                store_subtotal = sum(float(item.line_total) for item in store_lines)
                if store_subtotal > 0:
                    discount_share = (store_subtotal / eligible_subtotal) * discount_pool
            else:
                discount_share = (subtotal / eligible_subtotal) * discount_pool
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
