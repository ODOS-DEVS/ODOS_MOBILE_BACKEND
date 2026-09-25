"""Creating deliveries and putting them in front of riders.

Before this existed, nothing in the codebase ever constructed a DeliveryOffer
row -- `grep -rn "DeliveryOffer("` returned only the class definition -- so the
rider app's pool was guaranteed to be empty in production no matter how well
the claim path worked.

The entry point is a vendor explicitly asking ODOS to deliver an order. That is
deliberately opt-in rather than automatic on every order: today a vendor
dispatches to their own rider and `delivery_status` goes straight to
out_for_delivery, and that flow is live. Making every `ready` order jump into
the courier pool would change the meaning of an existing action for every
vendor on the platform. Opt-in leaves the existing path exactly as it is.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    TERMINAL_STATUSES,
    Courier,
    Delivery,
    DeliveryOffer,
    DeliveryStatus,
    Order,
    OrderItem,
    Store,
    User,
)
from app.services.delivery_state_machine import apply_transition

#: How long a rider has to claim an offer before the sweep retires it and the
#: delivery goes back to pending_assignment for admin attention. The value the
#: courier controller previously carried as OFFER_SLA_MINUTES, now applied
#: rather than merely declared.
OFFER_SLA_MINUTES = 15


def _order_store(db: Session, order: Order) -> Store | None:
    """The store this order is collected from.

    ODOS orders can in principle span vendors; a delivery leg belongs to one
    store, so this takes the first item's store. Multi-store orders become
    multiple deliveries later -- the model already allows it (one delivery row
    per leg), this function is simply where that decision will be made.
    """
    store_id = db.scalar(
        select(OrderItem.store_id)
        .where(OrderItem.order_id == order.id, OrderItem.store_id.is_not(None))
        .order_by(OrderItem.created_at.asc())
        .limit(1)
    )
    if not store_id:
        return None
    return db.get(Store, store_id)


def active_delivery_for_order(db: Session, order_id: uuid.UUID) -> Delivery | None:
    return db.scalar(
        select(Delivery)
        .where(Delivery.order_id == order_id, Delivery.status.not_in(TERMINAL_STATUSES))
        .limit(1)
    )


def create_delivery_for_order(
    db: Session, order: Order, *, actor: User, actor_type: str = "vendor"
) -> Delivery:
    """Create the fulfilment record for an order, or return the live one.

    Snapshots both addresses at creation. `dropoff_area` (city, region) is what
    a rider may see before claiming; `dropoff_address` (full street) is
    released only once they hold it. Splitting them here is what lets the read
    path enforce that boundary instead of trusting a client to redact.
    """
    existing = active_delivery_for_order(db, order.id)
    if existing:
        return existing

    if order.status in {"cancelled", "refunded"}:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="This order can no longer be delivered.",
        )
    if order.delivery_status == "delivered" or order.status == "delivered":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="This order has already been delivered.",
        )

    store = _order_store(db, order)
    delivery = Delivery(
        order_id=order.id,
        vendor_id=store.id if store else None,
        status=DeliveryStatus.PENDING_ASSIGNMENT.value,
        pickup_name=store.title if store else None,
        pickup_address=store.address if store else None,
        pickup_latitude=store.latitude if store else None,
        pickup_longitude=store.longitude if store else None,
        dropoff_area=", ".join(
            part for part in (order.address_city, order.address_region) if part
        )
        or None,
        dropoff_address=", ".join(
            part
            for part in (order.address_street, order.address_city, order.address_region)
            if part
        )
        or None,
    )
    db.add(delivery)
    db.flush()

    db.add(
        _event(
            delivery,
            event="created",
            actor_type=actor_type,
            actor_user_id=actor.id,
            context={"order_number": order.order_number},
        )
    )
    return delivery


def _event(delivery: Delivery, **kwargs):
    from app.models import DeliveryEvent

    return DeliveryEvent(delivery_id=delivery.id, **kwargs)


def _has_dedicated_fleet(db: Session, vendor_id: str | None) -> bool:
    """Does this vendor run their own riders on ODOS?"""
    if not vendor_id:
        return False
    return bool(
        db.scalar(
            select(Courier.id).where(Courier.vendor_id == vendor_id).limit(1)
        )
    )


def open_offer(
    db: Session,
    delivery: Delivery,
    *,
    actor: User | None = None,
    actor_type: str = "system",
    sla_minutes: int = OFFER_SLA_MINUTES,
    force_open_pool: bool = False,
) -> DeliveryOffer:
    """Put a delivery in front of riders.

    Two fields named vendor_id, meaning different things -- this is the exact
    place that distinction has to be got right:

      Delivery.vendor_id      which store the goods are collected from. Always set.
      DeliveryOffer.vendor_id who is allowed to take it. Set => that vendor's
                              own riders only. NULL => the ODOS open pool.

    Copying the first into the second (which an earlier version of this
    function did) scopes every offer to a vendor and leaves the open pool
    permanently empty -- the same class of bug as never creating offers at all,
    just harder to see.

    So the rule is explicit: a vendor that runs its own fleet gets first
    refusal; everyone else's work goes straight to the open pool. When a
    vendor-scoped offer expires unclaimed it escalates to the open pool rather
    than stalling, which is what "an order cannot sit unclaimed" requires.

    A delivery can be offered many times over its life. The partial unique
    index on (delivery_id) WHERE status='open' stops two open offers for the
    same delivery existing at once, so this doesn't police that itself -- but
    it does close any offer still open, so a retry never trips the index.
    """
    now = datetime.now(UTC)

    stale = db.scalars(
        select(DeliveryOffer).where(
            DeliveryOffer.delivery_id == delivery.id, DeliveryOffer.status == "open"
        )
    ).all()
    for offer in stale:
        offer.status = "closed"
        offer.closed_reason = "superseded"
        offer.closed_at = now

    if delivery.status == DeliveryStatus.PENDING_ASSIGNMENT.value:
        apply_transition(
            db,
            delivery,
            "offer",
            actor_type=actor_type,
            actor_user_id=actor.id if actor else None,
        )

    scope_to_vendor = (
        None
        if force_open_pool
        else (delivery.vendor_id if _has_dedicated_fleet(db, delivery.vendor_id) else None)
    )

    offer = DeliveryOffer(
        order_id=delivery.order_id,
        delivery_id=delivery.id,
        vendor_id=scope_to_vendor,
        status="open",
        sla_deadline=now + timedelta(minutes=sla_minutes),
    )
    db.add(offer)
    db.flush()
    return offer


def request_courier_for_order(db: Session, order: Order, *, actor: User) -> Delivery:
    """Vendor-facing entry point: 'ODOS, please deliver this one.'

    Creates the delivery, and offers it immediately if the order is already
    packed. If it isn't yet, the offer is created the moment the vendor marks
    it ready -- see offer_ready_order.
    """
    delivery = create_delivery_for_order(db, order, actor=actor)

    if (
        order.vendor_status in {"ready", "out_for_delivery"}
        and delivery.status == DeliveryStatus.PENDING_ASSIGNMENT.value
    ):
        open_offer(db, delivery, actor=actor, actor_type="vendor")

    return delivery


def offer_ready_order(db: Session, order: Order, *, actor: User) -> DeliveryOffer | None:
    """Called when a vendor moves an order to `ready`.

    Returns None -- and does nothing at all -- for orders the vendor never
    asked ODOS to deliver. That is what keeps the existing vendor-dispatch flow
    byte-for-byte unchanged for everyone who hasn't opted in.
    """
    delivery = active_delivery_for_order(db, order.id)
    if delivery is None:
        return None
    if delivery.status != DeliveryStatus.PENDING_ASSIGNMENT.value:
        return None
    return open_offer(db, delivery, actor=actor, actor_type="vendor")


def expire_stale_offers(db: Session, *, now: datetime | None = None) -> int:
    """Retire offers nobody claimed in time.

    An expired offer sends its delivery back to pending_assignment rather than
    killing it: 'an order cannot sit unclaimed' means it needs to become
    visible to admin assignment, not disappear. Returns how many were retired.
    """
    now = now or datetime.now(UTC)
    stale = db.scalars(
        select(DeliveryOffer).where(
            DeliveryOffer.status == "open", DeliveryOffer.sla_deadline < now
        )
    ).all()

    for offer in stale:
        offer.status = "expired"
        offer.closed_reason = "expired"
        offer.closed_at = now
        delivery = db.get(Delivery, offer.delivery_id) if offer.delivery_id else None
        if delivery and delivery.status == DeliveryStatus.OFFERED.value:
            apply_transition(
                db,
                delivery,
                "expire_offer",
                actor_type="system",
                context={"offer_id": str(offer.id)},
            )
            if offer.vendor_id is not None:
                # A vendor's own riders had first refusal and didn't take it.
                # Escalate to the open pool instead of leaving it to stall --
                # an order must not sit unclaimed.
                open_offer(db, delivery, actor_type="system", force_open_pool=True)

    if stale:
        # Flush, don't commit: the caller owns the transaction, but a caller
        # that re-reads these rows in the same session must see the change.
        db.flush()
    return len(stale)
