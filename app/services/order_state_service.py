"""The authority on what an order may become.

An ODOS order carries four independent facts, and conflating them is what
produced orders recorded as `payment_status="cancelled"` — a value that answers
neither "was money taken?" nor "is this order alive?".

    status               where the order is in its life
    payment_status       whether ODOS holds the money
    cancellation_status  whether someone is trying to stop it
    refund_status        whether money is on its way back

They move independently. A cancelled order whose payment succeeded and whose
refund is still in flight is `cancelled / paid / completed / processing`, and
every part of that is true at once.

Nothing here writes to the database. Callers ask whether a move is allowed and
why not; the controllers do the writing inside their own transactions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.models import Order

# ---------------------------------------------------------------------------
# The four dimensions
# ---------------------------------------------------------------------------

#: Order status. `pending_payment` is the entry point for every order, because
#: ODOS takes payment before fulfilment without exception.
ORDER_PENDING_PAYMENT = "pending_payment"
ORDER_PROCESSING = "processing"
ORDER_DELIVERED = "delivered"
ORDER_COMPLETED = "completed"
ORDER_CANCELLED = "cancelled"
ORDER_EXPIRED = "expired"
ORDER_PAYMENT_FAILED = "payment_failed"

#: Payment status. Only `paid` means ODOS verified it holds the money.
PAYMENT_PENDING = "pending"
PAYMENT_PAID = "paid"
PAYMENT_FAILED = "failed"
PAYMENT_EXPIRED = "expired"

#: Cancellation status, independent of whether the order died.
CANCEL_NONE = "none"
CANCEL_REQUESTED = "requested"
CANCEL_COMPLETED = "completed"
CANCEL_REJECTED = "rejected"

#: Refund status, independent of cancellation. A cancelled order whose money
#: has not come back is `cancelled` + `processing`, and saying so is the point.
REFUND_NOT_APPLICABLE = "not_applicable"
REFUND_PENDING = "pending"
REFUND_APPROVED = "approved"
REFUND_PROCESSING = "processing"
REFUND_SUCCESSFUL = "successful"
REFUND_FAILED = "failed"
REFUND_PARTIAL = "partially_refunded"

#: Vendor fulfilment stages, mirroring the mobile client's own ladder.
FULFILMENT_LADDER = ("pending", "confirmed", "processing", "ready", "out_for_delivery")

#: Fulfilment stages at which the vendor has not yet committed labour. Your
#: rule: a shopper may cancel freely any time before the vendor accepts.
BEFORE_VENDOR_ACCEPTS = frozenset({"pending"})

#: Past this the parcel has left the vendor, so cancellation becomes a
#: delivery-interception or return question rather than a cancellation one.
AFTER_DISPATCH = frozenset({"out_for_delivery", "delivered"})


@dataclass(frozen=True)
class Decision:
    """Whether a move is allowed, and the sentence to show if not."""

    allowed: bool
    reason: str | None = None
    #: True when the action is legal but needs someone else to approve it.
    requires_review: bool = False

    def __bool__(self) -> bool:
        return self.allowed


ALLOWED = Decision(True)


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------

def payment_is_settled(order: Order) -> bool:
    return _norm(order.payment_status) in {PAYMENT_PAID, "success", "successful"}


def payment_is_in_flight(order: Order, *, attempt_open: bool = True) -> bool:
    """The provider has been asked and has not answered.

    Treated as neither paid nor failed on purpose: this is the window where a
    cancellation must be held rather than resolved, because deciding either way
    would be a guess about money.

    `attempt_open` is what separates this from "unpaid and untouched". A
    shopper who reached checkout and walked away has no transaction pending and
    should be able to cancel outright; one whose mobile-money prompt is still
    on their phone must not have the order torn out from under the payment.
    The caller establishes that from the payment records.
    """
    return (
        attempt_open
        and _norm(order.payment_status) == PAYMENT_PENDING
        and _norm(order.status) == ORDER_PENDING_PAYMENT
    )


def order_is_closed(order: Order) -> bool:
    return _norm(order.status) in {
        ORDER_CANCELLED,
        ORDER_EXPIRED,
        ORDER_PAYMENT_FAILED,
    }


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def vendor_stage(order: Order) -> str:
    return _norm(order.vendor_status) or "pending"


def can_request_cancellation(
    order: Order,
    *,
    actor_role: str = "customer",
    payment_attempt_open: bool = False,
) -> Decision:
    """Whether this order can be cancelled, and whether anyone must agree.

    The policy, as configured for ODOS:

    * Unpaid — cancel outright. Nothing has been taken and no one has worked.
    * Payment in flight — accept the *request*, but hold it. The provider has
      not answered, and assuming failure because a shopper got impatient would
      either strand their money or hand them free goods.
    * Paid, vendor has not accepted — cancel outright, refund follows.
    * Paid, vendor accepted — allowed but needs the vendor's agreement; they
      have begun work.
    * Dispatched or delivered — not a cancellation any more. A parcel in a van
      cannot be un-sent, and calling it cancelled would be a lie.
    """
    if _norm(order.cancellation_status) == CANCEL_REQUESTED:
        return Decision(False, "A cancellation request for this order is already being reviewed.")
    if _norm(order.cancellation_status) == CANCEL_COMPLETED or _norm(order.status) == ORDER_CANCELLED:
        return Decision(False, "This order has already been cancelled.")
    if order_is_closed(order):
        return Decision(
            False,
            f"This order is {_norm(order.status).replace('_', ' ')} and cannot be cancelled.",
        )

    stage = vendor_stage(order)
    if stage in AFTER_DISPATCH or _norm(order.status) in {ORDER_DELIVERED, ORDER_COMPLETED}:
        if _norm(order.status) in {ORDER_DELIVERED, ORDER_COMPLETED}:
            return Decision(
                False,
                "This order has been delivered. Start a return instead and we'll refund you "
                "once it's back.",
            )
        return Decision(
            False,
            "This order is already on its way. Contact support to try to intercept it, or "
            "refuse the delivery and we'll refund you.",
        )

    if actor_role == "admin":
        return ALLOWED

    if payment_is_in_flight(order, attempt_open=payment_attempt_open):
        # Recorded, not resolved. The reconciliation job finishes this once the
        # provider says something definite.
        return Decision(
            True,
            "Your payment is still being confirmed. We'll finish the cancellation as soon as "
            "we know the outcome.",
            requires_review=True,
        )

    if not payment_is_settled(order):
        return ALLOWED

    if stage in BEFORE_VENDOR_ACCEPTS:
        return ALLOWED

    return Decision(
        True,
        "The seller has already started preparing this order, so they need to approve the "
        "cancellation.",
        requires_review=True,
    )


def can_decide_cancellation(order: Order) -> Decision:
    if _norm(order.cancellation_status) != CANCEL_REQUESTED:
        return Decision(False, "There is no cancellation request awaiting a decision.")
    return ALLOWED


# ---------------------------------------------------------------------------
# Fulfilment
# ---------------------------------------------------------------------------

def can_vendor_advance(order: Order, next_stage: str) -> Decision:
    """Whether a vendor may move their package to `next_stage`.

    Payment first, then the order being alive, then the ladder. Cancelling is
    always permitted: a vendor must be able to stand down, and refusing would
    strand the order.
    """
    target = _norm(next_stage)
    if target == "cancelled":
        return ALLOWED

    if order_is_closed(order):
        return Decision(
            False,
            f"This order is {_norm(order.status).replace('_', ' ')} and cannot be fulfilled.",
        )
    if not payment_is_settled(order):
        return Decision(
            False,
            "Payment for this order has not been confirmed, so it cannot be fulfilled yet.",
        )
    if _norm(order.cancellation_status) == CANCEL_REQUESTED:
        return Decision(
            False,
            "The buyer has asked to cancel this order. Respond to the request before "
            "continuing to prepare it.",
        )

    current = vendor_stage(order)
    if target == current:
        return ALLOWED
    if target not in FULFILMENT_LADDER:
        return Decision(False, "That fulfilment stage is not supported.")
    try:
        step = FULFILMENT_LADDER.index(target) - FULFILMENT_LADDER.index(current)
    except ValueError:
        return Decision(False, "That fulfilment stage is not supported.")
    if step != 1:
        return Decision(
            False,
            "Orders move one stage at a time, and cannot go backwards.",
        )
    return ALLOWED


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

def refund_is_owed(order: Order) -> bool:
    """Money only comes back if money went out."""
    return payment_is_settled(order)


def can_mark_refund_successful(provider_reference: str | None) -> Decision:
    """KutanaPay exposes no refund endpoint, so the money moves by hand.

    A reference is therefore mandatory: it is the only evidence that anything
    actually happened, and without it "refunded" is just a word someone typed.
    """
    if not (provider_reference or "").strip():
        return Decision(
            False,
            "Enter the transfer reference before marking this refund as paid out.",
        )
    return ALLOWED


def seconds_until_payment_expiry(order: Order, window_seconds: int) -> int:
    created = order.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if not created:
        return window_seconds
    elapsed = (datetime.now(UTC) - created).total_seconds()
    return max(0, int(window_seconds - elapsed))
