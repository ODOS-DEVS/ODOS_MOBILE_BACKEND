"""Courier-facing controller: profile, online status, the delivery pool, and
claiming.

require_courier_access mirrors require_vendor_access exactly -- same shape,
same reasoning: an admin may act on the app's behalf, a suspended account is
explicitly rejected with a reason rather than falling through to "not found,"
and everyone else needs the approved status.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Courier,
    CourierStatus,
    DeliveryOffer,
    Order,
    Store,
    User,
    UserRole,
    VehicleType,
)
from app.schemas.courier import (
    CourierLocationUpdate,
    CourierProfileCreate,
    CourierProfileRead,
    CourierStatusUpdate,
    DeliveryOfferRead,
)

# How long an offer stays claimable before the admin-ops sweep should flag it.
# Not enforced here -- this is what a future sweep job compares sla_deadline
# against -- but the value has to live somewhere, and it belongs next to the
# thing it bounds rather than buried in a constant file nothing else reads.
OFFER_SLA_MINUTES = 15


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


def fetch_courier_profile(db: Session, user: User) -> CourierProfileRead:
    require_courier_access(user)
    courier = _get_courier(db, user)
    if not courier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Set up your courier profile before going online.",
        )
    return CourierProfileRead.model_validate(courier)


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
    require_courier_access(user)
    courier = _get_courier(db, user)
    if not courier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Set up your courier profile before going online.",
        )
    courier.is_online = payload.is_online
    db.commit()
    db.refresh(courier)
    return CourierProfileRead.model_validate(courier)


def update_courier_location(
    db: Session, user: User, payload: CourierLocationUpdate
) -> None:
    require_courier_access(user)
    courier = _get_courier(db, user)
    if not courier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Set up your courier profile before sharing location.",
        )
    courier.current_latitude = payload.latitude
    courier.current_longitude = payload.longitude
    courier.location_updated_at = datetime.now(UTC)
    db.commit()


def _serialize_offer(db: Session, offer: DeliveryOffer) -> DeliveryOfferRead:
    order = offer.order
    vendor_name = None
    if offer.vendor_id:
        vendor_name = db.scalar(select(Store.title).where(Store.id == offer.vendor_id))
    return DeliveryOfferRead(
        id=offer.id,
        order_id=order.id,
        order_number=order.order_number,
        vendor_id=offer.vendor_id,
        vendor_name=vendor_name,
        dropoff_address=", ".join(
            part
            for part in (order.address_street, order.address_city, order.address_region)
            if part
        ),
        subtotal_amount=order.subtotal_amount,
        status=offer.status,
        sla_deadline=offer.sla_deadline,
        created_at=offer.created_at,
    )


def list_delivery_pool(db: Session, user: User) -> list[DeliveryOfferRead]:
    """Open offers this courier may claim.

    A pool-scoped courier (vendor_id is null on their profile) sees every
    open offer with no vendor restriction. A vendor-dedicated courier sees
    only that vendor's own offers -- the hybrid-fleet boundary from the
    design doc, enforced here rather than trusted from the client.
    """
    require_courier_access(user)
    courier = _get_courier(db, user)
    if not courier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Set up your courier profile before viewing the pool.",
        )

    query = (
        select(DeliveryOffer)
        .where(DeliveryOffer.status == "open")
        .order_by(DeliveryOffer.sla_deadline.asc())
    )
    if courier.vendor_id:
        query = query.where(DeliveryOffer.vendor_id == courier.vendor_id)

    offers = list(db.scalars(query).all())
    return [_serialize_offer(db, offer) for offer in offers]


def claim_delivery_offer(
    db: Session, user: User, offer_id: uuid.UUID
) -> DeliveryOfferRead:
    """The claim mechanic from the design doc.

    SELECT ... FOR UPDATE SKIP LOCKED, not FOR UPDATE alone: the difference
    is what a second courier sees when they tap the same offer at the same
    moment. FOR UPDATE would make their request *wait* on the first
    courier's transaction and then still fail once it commits, which reads
    as the app hanging. SKIP LOCKED lets the query simply not return a row
    already locked by someone else, so the second courier sees "gone"
    immediately, correctly, without waiting on a stranger's transaction.
    """
    require_courier_access(user)
    courier = _get_courier(db, user)
    if not courier:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Set up your courier profile before claiming a delivery.",
        )
    if not courier.is_online:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Go online before claiming a delivery.",
        )

    offer = db.execute(
        select(DeliveryOffer)
        .where(DeliveryOffer.id == offer_id, DeliveryOffer.status == "open")
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()

    if not offer:
        # Either it never existed, it's not open, or another courier's
        # transaction is holding the row right now -- all three read the
        # same way to the loser: it's gone.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This delivery has already been claimed.",
        )

    if courier.vendor_id and offer.vendor_id != courier.vendor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This delivery isn't in your pool.",
        )

    offer.status = "claimed"
    offer.claimed_by_courier_id = courier.id
    offer.claimed_at = datetime.now(UTC)

    order = offer.order
    order.courier_id = courier.id
    order.courier_assigned_at = offer.claimed_at

    db.commit()
    db.refresh(offer)
    return _serialize_offer(db, offer)
