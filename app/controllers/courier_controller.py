"""Courier-facing controller: profile, availability, the pool, claiming, and
the delivery transitions a rider is allowed to trigger.

require_courier_access mirrors require_vendor_access exactly -- same shape,
same reasoning: an admin may act on the app's behalf, a suspended account is
explicitly rejected with a reason rather than falling through to "not found,"
and everyone else needs the approved status.

Three rules hold throughout this file:

1. A rider reads only their own deliveries. Every lookup filters on their
   courier id; nothing takes a delivery id and trusts it.
2. A rider never receives a status to set. They post an intent, and
   delivery_state_machine decides whether it is legal from where the delivery
   actually is.
3. A rider cannot complete anything. `complete_pickup` and `complete_delivery`
   are absent from COURIER_INTENTS, so the two transitions that would end a
   delivery -- and eventually release money -- are unreachable from this app
   until verification exists to guard them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    ACTIVE_STATUSES,
    Courier,
    CourierStatus,
    Delivery,
    DeliveryEvent,
    DeliveryOffer,
    OrderItem,
    Store,
    TERMINAL_STATUSES,
    User,
    UserRole,
    VehicleType,
)
from app.schemas.courier import (
    CourierLocationUpdate,
    CourierProfileCreate,
    CourierProfileRead,
    CourierStatusUpdate,
    DeliveryEventRead,
    DeliveryIntentRequest,
    DeliveryOfferRead,
    DeliveryRead,
    DeliveryTimelineRead,
)
from app.services.delivery_state_machine import (
    COURIER_INTENTS,
    apply_transition,
)


def require_courier_access(user: User) -> None:
    # Equality, not .value access: UserRole subclasses str, so this works
    # whether SQLAlchemy has round-tripped `role` through the DB's enum type
    # or it is still the raw string a caller just assigned in-session --
    # require_vendor_access uses the same style for the same reason.
    if user.role == UserRole.ADMIN:
        return
    if user.courier_status == CourierStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Courier access is currently suspended for this account.",
        )
    if user.courier_status != CourierStatus.APPROVED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is not an approved courier.",
        )


def _get_courier(db: Session, user: User) -> Courier | None:
    return db.scalar(select(Courier).where(Courier.user_id == user.id))


def _require_courier(db: Session, user: User) -> Courier:
    require_courier_access(user)
    courier = _get_courier(db, user)
    if not courier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Set up your courier profile first.",
        )
    return courier


def _item_count(db: Session, order_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.sum(OrderItem.quantity), 0)).where(
                OrderItem.order_id == order_id
            )
        )
        or 0
    )


# --------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------


def fetch_courier_profile(db: Session, user: User) -> CourierProfileRead:
    return CourierProfileRead.model_validate(_require_courier(db, user))


def create_courier_profile(
    db: Session, user: User, payload: CourierProfileCreate
) -> CourierProfileRead:
    require_courier_access(user)

    if _get_courier(db, user):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a courier profile.",
        )

    try:
        vehicle_type = VehicleType(payload.vehicle_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown vehicle type. Use one of: {', '.join(v.value for v in VehicleType)}.",
        ) from None

    courier = Courier(
        user_id=user.id,
        vehicle_type=vehicle_type,
        plate_number=payload.plate_number,
    )
    db.add(courier)
    db.commit()
    db.refresh(courier)
    return CourierProfileRead.model_validate(courier)


def update_courier_status(
    db: Session, user: User, payload: CourierStatusUpdate
) -> CourierProfileRead:
    courier = _require_courier(db, user)
    courier.is_online = payload.is_online
    db.commit()
    db.refresh(courier)
    return CourierProfileRead.model_validate(courier)


def update_courier_location(
    db: Session, user: User, payload: CourierLocationUpdate
) -> None:
    """Accepted only while the rider is actually carrying something.

    §20 of the delivery brief: do not track riders who are not performing
    delivery work. Enforcing that here rather than trusting the app to stop
    sending is the difference between a policy and a guarantee.
    """
    courier = _require_courier(db, user)

    has_active = db.scalar(
        select(Delivery.id)
        .where(
            Delivery.courier_id == courier.id,
            Delivery.status.in_(ACTIVE_STATUSES),
        )
        .limit(1)
    )
    if not has_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Location is only shared while you have an active delivery.",
        )

    courier.current_latitude = payload.latitude
    courier.current_longitude = payload.longitude
    courier.location_updated_at = datetime.now(UTC)
    db.commit()


# --------------------------------------------------------------------------
# The pool
# --------------------------------------------------------------------------


def _serialize_offer(db: Session, offer: DeliveryOffer) -> DeliveryOfferRead:
    """Pre-claim view. No street address, no customer, no order value."""
    delivery = db.get(Delivery, offer.delivery_id) if offer.delivery_id else None
    order = offer.order

    pickup_area = None
    if delivery and delivery.vendor_id:
        store = db.get(Store, delivery.vendor_id)
        if store:
            pickup_area = ", ".join(
                part for part in (store.city, store.region) if part
            ) or None

    return DeliveryOfferRead(
        id=offer.id,
        delivery_id=offer.delivery_id,
        order_number=order.order_number,
        vendor_id=offer.vendor_id,
        pickup_name=delivery.pickup_name if delivery else None,
        pickup_area=pickup_area,
        dropoff_area=(
            delivery.dropoff_area
            if delivery
            else ", ".join(
                part for part in (order.address_city, order.address_region) if part
            )
            or None
        ),
        item_count=_item_count(db, order.id),
        status=offer.status,
        sla_deadline=offer.sla_deadline,
        created_at=offer.created_at,
    )


def list_delivery_pool(db: Session, user: User) -> list[DeliveryOfferRead]:
    """Open offers this courier may claim.

    A vendor-dedicated courier sees only that vendor's offers. An open-pool
    courier sees only offers that are *not* scoped to a vendor -- the previous
    version let them see every vendor's dedicated work too, which defeated the
    point of a dedicated fleet.
    """
    courier = _require_courier(db, user)

    query = (
        select(DeliveryOffer)
        .where(
            DeliveryOffer.status == "open",
            DeliveryOffer.sla_deadline > datetime.now(UTC),
        )
        .order_by(DeliveryOffer.sla_deadline.asc())
    )
    if courier.vendor_id:
        query = query.where(DeliveryOffer.vendor_id == courier.vendor_id)
    else:
        query = query.where(DeliveryOffer.vendor_id.is_(None))

    return [_serialize_offer(db, offer) for offer in db.scalars(query).all()]


def claim_delivery_offer(
    db: Session, user: User, offer_id: uuid.UUID
) -> DeliveryRead:
    """The claim mechanic.

    SELECT ... FOR UPDATE SKIP LOCKED, not FOR UPDATE alone: the difference is
    what a second courier sees when they tap the same offer at the same moment.
    FOR UPDATE would make their request *wait* on the first courier's
    transaction and then still fail once it commits, which reads as the app
    hanging. SKIP LOCKED lets the query simply not return a row already locked
    by someone else, so the second courier sees "gone" immediately, correctly,
    without waiting on a stranger's transaction.
    """
    courier = _require_courier(db, user)
    if not courier.is_online:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Go online before claiming a delivery.",
        )

    already_holding = db.scalar(
        select(func.count(Delivery.id)).where(
            Delivery.courier_id == courier.id, Delivery.status.in_(ACTIVE_STATUSES)
        )
    )
    if already_holding:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Finish the delivery you're carrying before claiming another.",
        )

    offer = db.execute(
        select(DeliveryOffer)
        .where(DeliveryOffer.id == offer_id, DeliveryOffer.status == "open")
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()

    if not offer:
        # Either it never existed, it's not open, or another courier's
        # transaction is holding the row right now -- all three read the same
        # way to the loser: it's gone.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This delivery has already been claimed.",
        )

    if courier.vendor_id and offer.vendor_id != courier.vendor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This delivery isn't in your pool.",
        )
    if not courier.vendor_id and offer.vendor_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This delivery is reserved for that vendor's own riders.",
        )
    if offer.sla_deadline <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This offer has expired.",
        )

    delivery = db.get(Delivery, offer.delivery_id) if offer.delivery_id else None
    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This delivery is no longer available.",
        )

    now = datetime.now(UTC)
    offer.status = "claimed"
    offer.claimed_by_courier_id = courier.id
    offer.claimed_at = now
    offer.closed_at = now

    delivery.courier_id = courier.id
    apply_transition(
        db,
        delivery,
        "claim",
        actor_type="courier",
        actor_user_id=user.id,
        context={"offer_id": str(offer.id)},
    )

    # Denormalized read path the vendor and admin views already query.
    order = delivery.order
    order.courier_id = courier.id
    order.courier_assigned_at = now

    db.commit()
    db.refresh(delivery)
    return _serialize_delivery(db, delivery)


# --------------------------------------------------------------------------
# A rider's own deliveries
# --------------------------------------------------------------------------


def _serialize_delivery(db: Session, delivery: Delivery) -> DeliveryRead:
    order = delivery.order
    return DeliveryRead(
        id=delivery.id,
        order_id=delivery.order_id,
        order_number=order.order_number,
        status=delivery.status,
        vendor_id=delivery.vendor_id,
        pickup_name=delivery.pickup_name,
        pickup_address=delivery.pickup_address,
        pickup_latitude=delivery.pickup_latitude,
        pickup_longitude=delivery.pickup_longitude,
        dropoff_area=delivery.dropoff_area,
        dropoff_address=delivery.dropoff_address,
        dropoff_latitude=delivery.dropoff_latitude,
        dropoff_longitude=delivery.dropoff_longitude,
        # First name only. A rider needs to know who to hand it to, not the
        # customer's full identity.
        customer_name=(order.address_full_name or "").strip().split(" ")[0] or None,
        delivery_instructions=order.delivery_instructions,
        item_count=_item_count(db, order.id),
        attempt_count=delivery.attempt_count,
        failure_reason=delivery.failure_reason,
        offered_at=delivery.offered_at,
        accepted_at=delivery.accepted_at,
        arrived_at_pickup_at=delivery.arrived_at_pickup_at,
        picked_up_at=delivery.picked_up_at,
        arrived_at_customer_at=delivery.arrived_at_customer_at,
        delivered_at=delivery.delivered_at,
        created_at=delivery.created_at,
    )


def _own_delivery(db: Session, courier: Courier, delivery_id: uuid.UUID) -> Delivery:
    """Load a delivery *scoped to this rider*.

    The courier id is part of the query, not checked afterwards, so there is no
    version of this that returns someone else's delivery. A rider asking for an
    id that isn't theirs gets the same 404 as an id that doesn't exist -- it
    tells them nothing about whether it is real.
    """
    delivery = db.scalar(
        select(Delivery).where(
            Delivery.id == delivery_id, Delivery.courier_id == courier.id
        )
    )
    if delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Delivery not found."
        )
    return delivery


def fetch_active_delivery(db: Session, user: User) -> DeliveryRead | None:
    courier = _require_courier(db, user)
    delivery = db.scalar(
        select(Delivery)
        .where(
            Delivery.courier_id == courier.id, Delivery.status.in_(ACTIVE_STATUSES)
        )
        .order_by(Delivery.accepted_at.desc())
        .limit(1)
    )
    return _serialize_delivery(db, delivery) if delivery else None


def fetch_delivery(db: Session, user: User, delivery_id: uuid.UUID) -> DeliveryTimelineRead:
    courier = _require_courier(db, user)
    delivery = _own_delivery(db, courier, delivery_id)
    events = db.scalars(
        select(DeliveryEvent)
        .where(DeliveryEvent.delivery_id == delivery.id)
        .order_by(DeliveryEvent.occurred_at.asc())
    ).all()
    return DeliveryTimelineRead(
        delivery=_serialize_delivery(db, delivery),
        events=[DeliveryEventRead.model_validate(event) for event in events],
    )


def list_delivery_history(
    db: Session, user: User, *, limit: int = 50
) -> list[DeliveryRead]:
    courier = _require_courier(db, user)
    deliveries = db.scalars(
        select(Delivery)
        .where(
            Delivery.courier_id == courier.id,
            Delivery.status.in_(TERMINAL_STATUSES),
        )
        .order_by(Delivery.updated_at.desc())
        .limit(min(limit, 100))
    ).all()
    return [_serialize_delivery(db, delivery) for delivery in deliveries]


def advance_delivery(
    db: Session,
    user: User,
    delivery_id: uuid.UUID,
    intent: str,
    payload: DeliveryIntentRequest | None = None,
) -> DeliveryRead:
    """Apply a rider-triggered transition.

    The intent must be one a rider is allowed to trigger at all, and legal from
    the delivery's current state. Both checks are here rather than in the
    route, so no future endpoint can bypass them by calling this differently.
    """
    courier = _require_courier(db, user)
    delivery = _own_delivery(db, courier, delivery_id)

    if intent not in COURIER_INTENTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That action isn't available from the rider app.",
        )

    payload = payload or DeliveryIntentRequest()
    context: dict[str, str] = {}
    if payload.reason:
        context["reason"] = payload.reason
    if payload.note:
        context["note"] = payload.note

    apply_transition(
        db,
        delivery,
        intent,
        actor_type="courier",
        actor_user_id=user.id,
        context=context or None,
    )

    if intent in {"fail_delivery", "report_customer_unavailable"}:
        delivery.failure_reason = payload.reason
        delivery.failure_note = payload.note
        delivery.attempt_count = (delivery.attempt_count or 0) + 1

    if intent == "release":
        # Handing it back: the delivery returns to the pool and stops being
        # this rider's. The order's denormalized pointer has to let go too, or
        # the vendor view keeps naming a rider who walked away.
        delivery.courier_id = None
        delivery.order.courier_id = None
        delivery.order.courier_assigned_at = None

    if intent == "arrive_at_pickup":
        delivery.arrived_at_pickup_at = delivery.arrived_at_pickup_at or datetime.now(UTC)

    db.commit()
    db.refresh(delivery)
    return _serialize_delivery(db, delivery)
