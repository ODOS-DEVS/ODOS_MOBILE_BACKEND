"""The only table of legal delivery transitions.

The rider app never sends a status. It sends an *intent* -- "I've arrived at
pickup" -- and this module decides whether that intent is legal from where the
delivery actually is, and what it becomes. Anything else would let a client
post `status="delivered"` and skip the whole flow, which is the exact failure
mode ODOS already refused to allow for vendors (see
delivery_lifecycle_service._complete_delivery: `delivered` is set in one place
and never by the party doing the fulfilling).

Two rules this module enforces that are easy to lose later:

1. DELIVERED is not reachable by any rider intent. It is reachable only from
   DELIVERY_VERIFICATION, and only through the verification path built in
   Phase 3 -- a rider pressing a button must never be sufficient to complete a
   delivery, because completion is what eventually releases money.

2. Every transition is recorded. A move that isn't written to delivery_events
   didn't happen as far as the audit trail is concerned, so recording is done
   here rather than left to each caller to remember.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy.orm import Session

from app.models import Delivery, DeliveryEvent, DeliveryStatus

S = DeliveryStatus

#: intent -> (allowed source states, resulting state)
#:
#: Intents are what an actor asks for; the value is what the delivery becomes.
#: Keeping this as data rather than branching code means the legal moves can be
#: read -- and tested -- without following control flow.
TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    # --- assignment -------------------------------------------------------
    "offer": (frozenset({S.PENDING_ASSIGNMENT.value}), S.OFFERED.value),
    "claim": (frozenset({S.OFFERED.value}), S.ACCEPTED.value),
    "assign": (
        frozenset({S.PENDING_ASSIGNMENT.value, S.OFFERED.value}),
        S.ACCEPTED.value,
    ),
    # --- to the vendor ----------------------------------------------------
    "start_pickup": (frozenset({S.ACCEPTED.value}), S.EN_ROUTE_TO_PICKUP.value),
    "arrive_at_pickup": (
        frozenset({S.ACCEPTED.value, S.EN_ROUTE_TO_PICKUP.value}),
        S.ARRIVED_AT_PICKUP.value,
    ),
    "begin_pickup_verification": (
        frozenset({S.ARRIVED_AT_PICKUP.value}),
        S.PICKUP_VERIFICATION.value,
    ),
    # Reached only by a verified handover (Phase 3). Listed here so the legal
    # move is declared in one place, but no rider-facing endpoint maps to it.
    "complete_pickup": (
        frozenset({S.PICKUP_VERIFICATION.value}),
        S.PICKED_UP.value,
    ),
    # --- to the customer --------------------------------------------------
    "start_dropoff": (
        frozenset({S.PICKED_UP.value, S.CUSTOMER_UNAVAILABLE.value}),
        S.EN_ROUTE_TO_CUSTOMER.value,
    ),
    "arrive_at_customer": (
        frozenset({S.PICKED_UP.value, S.EN_ROUTE_TO_CUSTOMER.value}),
        S.ARRIVED_AT_CUSTOMER.value,
    ),
    "begin_delivery_verification": (
        frozenset({S.ARRIVED_AT_CUSTOMER.value}),
        S.DELIVERY_VERIFICATION.value,
    ),
    # Phase 3 only, and only after the customer's code has been verified.
    "complete_delivery": (
        frozenset({S.DELIVERY_VERIFICATION.value}),
        S.DELIVERED.value,
    ),
    # --- exceptions -------------------------------------------------------
    "report_customer_unavailable": (
        frozenset({S.ARRIVED_AT_CUSTOMER.value, S.DELIVERY_VERIFICATION.value}),
        S.CUSTOMER_UNAVAILABLE.value,
    ),
    "fail_delivery": (
        frozenset(
            {
                S.PICKED_UP.value,
                S.EN_ROUTE_TO_CUSTOMER.value,
                S.ARRIVED_AT_CUSTOMER.value,
                S.DELIVERY_VERIFICATION.value,
                S.CUSTOMER_UNAVAILABLE.value,
            }
        ),
        S.DELIVERY_FAILED.value,
    ),
    "require_return": (
        frozenset({S.DELIVERY_FAILED.value, S.CUSTOMER_UNAVAILABLE.value}),
        S.RETURN_REQUIRED.value,
    ),
    "start_return": (frozenset({S.RETURN_REQUIRED.value}), S.RETURNING_TO_VENDOR.value),
    "complete_return": (
        frozenset({S.RETURNING_TO_VENDOR.value}),
        S.RETURNED.value,
    ),
    # --- release / abandon ------------------------------------------------
    # A rider giving a delivery back. It goes to the pool again rather than
    # dying, which is only expressible because offers are now per-delivery.
    "release": (
        frozenset(
            {
                S.ACCEPTED.value,
                S.EN_ROUTE_TO_PICKUP.value,
                S.ARRIVED_AT_PICKUP.value,
                S.PICKUP_VERIFICATION.value,
            }
        ),
        S.PENDING_ASSIGNMENT.value,
    ),
    "expire_offer": (frozenset({S.OFFERED.value}), S.PENDING_ASSIGNMENT.value),
    "cancel": (
        frozenset(
            {
                S.PENDING_ASSIGNMENT.value,
                S.OFFERED.value,
                S.ACCEPTED.value,
                S.EN_ROUTE_TO_PICKUP.value,
                S.ARRIVED_AT_PICKUP.value,
                S.PICKUP_VERIFICATION.value,
            }
        ),
        S.CANCELLED.value,
    ),
    "dispute": (
        frozenset({S.DELIVERY_FAILED.value, S.RETURN_REQUIRED.value, S.RETURNED.value}),
        S.DISPUTED.value,
    ),
}

#: Intents a rider may trigger from their own app. Everything else in
#: TRANSITIONS is reachable only by the system, a vendor, or an admin.
#: `complete_pickup` and `complete_delivery` are deliberately absent.
COURIER_INTENTS: frozenset[str] = frozenset(
    {
        "start_pickup",
        "arrive_at_pickup",
        "begin_pickup_verification",
        "start_dropoff",
        "arrive_at_customer",
        "begin_delivery_verification",
        "report_customer_unavailable",
        "fail_delivery",
        "start_return",
        "release",
    }
)

#: Timestamp column set when a delivery reaches a state, so the timeline is
#: queryable without walking the event log.
_STAMPS: dict[str, str] = {
    S.OFFERED.value: "offered_at",
    S.ACCEPTED.value: "accepted_at",
    S.ARRIVED_AT_PICKUP.value: "arrived_at_pickup_at",
    S.PICKED_UP.value: "picked_up_at",
    S.ARRIVED_AT_CUSTOMER.value: "arrived_at_customer_at",
    S.DELIVERED.value: "delivered_at",
    S.CANCELLED.value: "cancelled_at",
    S.RETURNED.value: "returned_at",
}


class InvalidTransition(HTTPException):
    """409, not 400: the request was well-formed, the delivery just isn't in a
    state where it makes sense. A rider whose app is a step behind should be
    told what happened, not told they sent garbage."""

    def __init__(self, intent: str, current: str) -> None:
        super().__init__(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=(
                f"You can't {intent.replace('_', ' ')} while this delivery is "
                f"{current.replace('_', ' ')}."
            ),
        )
        self.intent = intent
        self.current = current


def can_apply(intent: str, current_status: str) -> bool:
    rule = TRANSITIONS.get(intent)
    return bool(rule) and current_status in rule[0]


def apply_transition(
    db: Session,
    delivery: Delivery,
    intent: str,
    *,
    actor_type: str,
    actor_user_id: uuid.UUID | None = None,
    context: dict | None = None,
) -> Delivery:
    """Move a delivery, or refuse. Writes the audit event either way it moves.

    Does not commit -- the caller owns the transaction, so a transition and the
    side effects that must accompany it (closing an offer, stamping the order)
    land together or not at all.
    """
    rule = TRANSITIONS.get(intent)
    if rule is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown delivery action '{intent}'.",
        )

    allowed_from, next_status = rule
    if delivery.status not in allowed_from:
        raise InvalidTransition(intent, delivery.status)

    previous = delivery.status
    delivery.status = next_status

    stamp = _STAMPS.get(next_status)
    if stamp and getattr(delivery, stamp, None) is None:
        setattr(delivery, stamp, datetime.now(UTC))

    db.add(
        DeliveryEvent(
            delivery_id=delivery.id,
            event=intent,
            from_status=previous,
            to_status=next_status,
            actor_type=actor_type,
            actor_user_id=actor_user_id,
            context=context,
        )
    )
    return delivery
