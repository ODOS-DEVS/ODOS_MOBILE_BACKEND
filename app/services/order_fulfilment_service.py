"""The one answer to "may a vendor act on this order?".

Every surface that shows a vendor an order, and every endpoint that lets a
vendor move one, asks this module. Having a single answer is the point: the
vendor list, the vendor detail view, the status endpoint and the notification
fan-out previously each decided for themselves, and three of the four decided
"yes" for an order nobody had paid for.

Deliberately not a status check. Fulfilment eligibility is a question about
money and about whether the order is still alive, and those live in different
columns from `vendor_status`.
"""

from __future__ import annotations

from app.models import Order

#: `payment_status` values that mean money has actually been verified as
#: received. "pending" is a payment in flight, "failed" speaks for itself, and
#: "cancelled" is written by the cancellation path — none of them are money.
PAID_PAYMENT_STATUSES = frozenset({"paid", "success", "successful"})

#: An order in one of these states is finished or void; no vendor should be
#: preparing, dispatching or otherwise touching it whatever its payment says.
CLOSED_ORDER_STATUSES = frozenset({"cancelled", "refunded", "expired", "payment_failed"})


def order_payment_is_settled(order: Order) -> bool:
    """True when ODOS has verified it holds the money for this order."""
    return (order.payment_status or "").strip().lower() in PAID_PAYMENT_STATUSES


def order_is_closed(order: Order) -> bool:
    return (order.status or "").strip().lower() in CLOSED_ORDER_STATUSES


def fulfilment_block_reason(order: Order) -> str | None:
    """Why a vendor may not act on this order, or None when they may.

    Returns the sentence shown to the vendor, so the refusal explains itself
    rather than arriving as a bare 400.
    """
    if order_is_closed(order):
        return (
            f"This order is {(order.status or 'closed').replace('_', ' ')} and cannot be "
            "fulfilled."
        )
    # No exemption for an unpaid order. ODOS does not offer cash on delivery —
    # every order is paid for before anyone picks it off a shelf — so there is
    # no legitimate shape of unpaid order a vendor should be working on.
    if not order_payment_is_settled(order):
        return (
            "Payment for this order has not been confirmed, so it cannot be fulfilled yet. "
            "It will appear in your orders once payment clears."
        )
    # A cancellation the vendor has not answered freezes the order. Letting
    # them keep packing while the buyer waits for a decision is how a parcel
    # ends up dispatched against a request to stop it.
    if (order.cancellation_status or "").strip().lower() == "requested":
        return (
            "The buyer has asked to cancel this order. Respond to the request before "
            "preparing it any further."
        )
    return None


def order_is_fulfilment_eligible(order: Order) -> bool:
    return fulfilment_block_reason(order) is None
