"""What a cancellation is worth, and the ledger that records paying it back.

Refund arithmetic on a marketplace order is not "give back what they paid".
A cart-wide voucher was earned by the whole basket, delivery was charged per
vendor package, and cancelling one vendor's part must not quietly hand the
shopper the discount the surviving items are still enjoying.

The approach here is the ordinary one — proportional allocation by line value,
the same method Shopify and Stripe use — because it is the only split that
leaves every surviving line paying exactly what it would have paid if the
cancelled line had never been in the basket.

ODOS policy, as configured:

* Delivery is refunded when the order is cancelled before dispatch.
* The shopper bears the payment-gateway fee on a refund.
* A voucher's minimum-spend is not re-tested after a partial cancellation.
  Clawing back a discount somebody already earned, because an unrelated vendor
  let them down, is hostile and creates more support load than it saves.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Order, OrderItem
from app.models.order_refund import OrderRefund
from app.services.finance_math import round_money


@dataclass(frozen=True)
class RefundQuote:
    """A refund worked out but not yet recorded."""

    amount: float
    items_amount: float
    delivery_amount: float
    discount_withheld: float
    gateway_fee_withheld: float
    #: True when this covers everything still owed on the order.
    is_full_order: bool

    def as_breakdown(self) -> dict[str, float]:
        return {
            "items": self.items_amount,
            "delivery": self.delivery_amount,
            "discount_withheld": self.discount_withheld,
            "gateway_fee_withheld": self.gateway_fee_withheld,
            "total": self.amount,
        }


def refund_reference() -> str:
    return f"RFD-{''.join(secrets.choice('0123456789') for _ in range(8))}"


def _line_total(item: OrderItem) -> float:
    return float(item.line_total or (item.unit_price or 0) * (item.quantity or 0))


def _scope_items(order: Order, vendor_user_id=None) -> list[OrderItem]:
    if vendor_user_id is None:
        return list(order.items)
    return [item for item in order.items if item.vendor_user_id == vendor_user_id]


def quote_refund(
    db: Session,
    order: Order,
    *,
    vendor_user_id=None,
    include_delivery: bool = True,
) -> RefundQuote:
    """Work out what is owed for cancelling all of an order, or one vendor's part.

    Discount is apportioned by each line's share of the pre-discount subtotal,
    so the shopper is refunded the cancelled items *net of the discount those
    items attracted* — never the sticker price of goods they got a deal on.

    Delivery is refunded only for the packages actually being cancelled. On a
    multi-vendor order the surviving packages still have to be driven to the
    door, and that still costs.
    """
    all_items = list(order.items)
    scope = _scope_items(order, vendor_user_id)
    if not scope:
        return RefundQuote(0.0, 0.0, 0.0, 0.0, 0.0, False)

    gross_all = sum(_line_total(item) for item in all_items)
    gross_scope = sum(_line_total(item) for item in scope)
    share = (gross_scope / gross_all) if gross_all > 0 else 0.0

    # The discount this part of the basket earned, and therefore does not get
    # back in cash.
    total_discount = float(order.discount_amount or 0)
    discount_withheld = round_money(total_discount * share)
    items_amount = round_money(gross_scope - discount_withheld)

    delivery_amount = 0.0
    if include_delivery:
        if vendor_user_id is None:
            delivery_amount = round_money(float(order.shipping_amount or 0))
        else:
            # Per-package where packages exist, because each vendor's delivery
            # was quoted on its own terms rather than split evenly.
            package = next(
                (p for p in (order.packages or []) if p.vendor_user_id == vendor_user_id),
                None,
            )
            delivery_amount = round_money(
                float(getattr(package, "shipping_amount", 0) or 0)
                if package is not None
                else float(order.shipping_amount or 0) * share
            )

    gross_refund = round_money(items_amount + delivery_amount)

    # The shopper bears the gateway fee, per ODOS policy. Rate is configurable
    # and defaults to zero so no fee is silently withheld until someone sets
    # the real KutanaPay rate.
    fee_rate = float(getattr(settings, "refund_gateway_fee_rate", 0.0) or 0.0)
    gateway_fee_withheld = round_money(gross_refund * fee_rate)

    already = refunded_total(db, order)
    payable = max(0.0, float(order.total_amount or 0) - already)
    amount = round_money(min(gross_refund - gateway_fee_withheld, payable))

    return RefundQuote(
        amount=amount,
        items_amount=items_amount,
        delivery_amount=delivery_amount,
        discount_withheld=discount_withheld,
        gateway_fee_withheld=gateway_fee_withheld,
        is_full_order=vendor_user_id is None,
    )


def refunded_total(db: Session, order: Order) -> float:
    """Everything already successfully returned on this order.

    Only successful refunds count. A pending one has not left ODOS's hands, and
    treating it as spent would block a legitimate second refund.
    """
    rows = db.scalars(
        select(OrderRefund).where(
            OrderRefund.order_id == order.id,
            OrderRefund.status == "successful",
        )
    ).all()
    return round_money(sum(float(row.amount or 0) for row in rows))


def open_refund_for(db: Session, order: Order, vendor_user_id=None) -> OrderRefund | None:
    """An in-flight refund for this scope, if one exists.

    Mirrors the partial unique index. Checked before creating so a duplicate
    surfaces as a clear message rather than an integrity error.
    """
    return db.scalar(
        select(OrderRefund).where(
            OrderRefund.order_id == order.id,
            OrderRefund.vendor_user_id == vendor_user_id,
            OrderRefund.status.in_(["pending", "approved", "processing"]),
        )
    )
