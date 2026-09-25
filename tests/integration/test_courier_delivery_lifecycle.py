"""What a rider can and cannot do to a delivery.

Every test here is about a boundary rather than a happy path, because the happy
path is the part that gets exercised by hand during development and the
boundaries are the part that doesn't. Each one corresponds to a specific way
the old shape could be abused or a specific thing it could not express:

- a rider reaching another rider's delivery (IDOR)
- a rider skipping stages, or completing a delivery without verification
- a released delivery being re-offered, which UNIQUE(order_id) made impossible
- location being accepted from a rider who isn't carrying anything
- the pool leaking the customer's address and basket value
- an open-pool rider taking a vendor's dedicated work
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from tests.conftest import requires_db

pytestmark = requires_db


def _courier(session, *, online: bool = True, vendor_id: str | None = None):
    from app.models import Courier, User

    user = User(
        full_name="Test Courier",
        email=f"courier-{uuid.uuid4().hex[:12]}@example.com",
        role="courier",
        courier_status="approved",
    )
    session.add(user)
    session.flush()
    courier = Courier(
        user_id=user.id, vehicle_type="bike", is_online=online, vendor_id=vendor_id
    )
    session.add(courier)
    session.flush()
    return user, courier


def _order(session):
    from app.models import Order, User

    buyer = User(
        full_name="Ama Mensah", email=f"buyer-{uuid.uuid4().hex[:12]}@example.com"
    )
    session.add(buyer)
    session.flush()
    order = Order(
        order_number=f"ORD-{uuid.uuid4().hex[:10].upper()}",
        user_id=buyer.id,
        subtotal_amount=1250.0,
        total_amount=1250.0,
        address_full_name="Ama Mensah",
        address_phone="0200000000",
        address_street="12 Independence Avenue",
        address_city="Accra",
        address_region="Greater Accra",
        payment_type="card",
        payment_label="Card",
    )
    session.add(order)
    session.flush()
    return order


def _delivery(session, order, *, status="offered", courier=None, vendor_id=None):
    from app.models import Delivery

    delivery = Delivery(
        order_id=order.id,
        vendor_id=vendor_id,
        courier_id=courier.id if courier else None,
        status=status,
        dropoff_area="Accra, Greater Accra",
        dropoff_address="12 Independence Avenue, Accra, Greater Accra",
        accepted_at=datetime.now(UTC),
    )
    session.add(delivery)
    session.flush()
    return delivery


def _offer(session, delivery, *, vendor_id=None, minutes=15):
    from app.models import DeliveryOffer

    offer = DeliveryOffer(
        order_id=delivery.order_id,
        delivery_id=delivery.id,
        vendor_id=vendor_id,
        status="open",
        sla_deadline=datetime.now(UTC) + timedelta(minutes=minutes),
    )
    session.add(offer)
    session.flush()
    return offer


# --------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------


def test_a_rider_cannot_read_another_riders_delivery(db):
    from app.controllers.courier_controller import fetch_delivery

    _owner_user, owner = _courier(db)
    intruder_user, _intruder = _courier(db)
    delivery = _delivery(db, _order(db), status="picked_up", courier=owner)

    with pytest.raises(HTTPException) as exc:
        fetch_delivery(db, intruder_user, delivery.id)

    # 404, not 403: telling a stranger "that exists but isn't yours" confirms
    # the id is real.
    assert exc.value.status_code == 404


def test_a_rider_cannot_advance_another_riders_delivery(db):
    from app.controllers.courier_controller import advance_delivery

    _owner_user, owner = _courier(db)
    intruder_user, _ = _courier(db)
    delivery = _delivery(db, _order(db), status="accepted", courier=owner)

    with pytest.raises(HTTPException) as exc:
        advance_delivery(db, intruder_user, delivery.id, "start_pickup")
    assert exc.value.status_code == 404

    db.refresh(delivery)
    assert delivery.status == "accepted"


# --------------------------------------------------------------------------
# The state machine
# --------------------------------------------------------------------------


def test_a_rider_cannot_skip_to_delivered(db):
    """The whole point of the state machine. `complete_delivery` is a real
    transition, but it is not one a rider may trigger -- it is reachable only
    from DELIVERY_VERIFICATION, by the verification path."""
    from app.controllers.courier_controller import advance_delivery

    user, courier = _courier(db)
    delivery = _delivery(db, _order(db), status="accepted", courier=courier)

    with pytest.raises(HTTPException) as exc:
        advance_delivery(db, user, delivery.id, "complete_delivery")
    assert exc.value.status_code == 403

    db.refresh(delivery)
    assert delivery.status == "accepted"
    assert delivery.delivered_at is None


def test_a_rider_cannot_complete_pickup_without_verification(db):
    from app.controllers.courier_controller import advance_delivery

    user, courier = _courier(db)
    delivery = _delivery(db, _order(db), status="pickup_verification", courier=courier)

    with pytest.raises(HTTPException) as exc:
        advance_delivery(db, user, delivery.id, "complete_pickup")
    assert exc.value.status_code == 403

    db.refresh(delivery)
    assert delivery.status == "pickup_verification"
    assert delivery.picked_up_at is None


def test_out_of_order_transitions_are_rejected(db):
    """Arriving at the customer before picking anything up."""
    from app.controllers.courier_controller import advance_delivery

    user, courier = _courier(db)
    delivery = _delivery(db, _order(db), status="accepted", courier=courier)

    with pytest.raises(HTTPException) as exc:
        advance_delivery(db, user, delivery.id, "arrive_at_customer")
    assert exc.value.status_code == 409

    db.refresh(delivery)
    assert delivery.status == "accepted"


def test_a_legal_transition_moves_and_records_an_event(db):
    from sqlalchemy import select

    from app.controllers.courier_controller import advance_delivery
    from app.models import DeliveryEvent

    user, courier = _courier(db)
    delivery = _delivery(db, _order(db), status="accepted", courier=courier)

    result = advance_delivery(db, user, delivery.id, "start_pickup")
    assert result.status == "en_route_to_pickup"

    events = db.scalars(
        select(DeliveryEvent).where(DeliveryEvent.delivery_id == delivery.id)
    ).all()
    assert [e.event for e in events] == ["start_pickup"]
    assert events[0].from_status == "accepted"
    assert events[0].to_status == "en_route_to_pickup"
    assert events[0].actor_type == "courier"


# --------------------------------------------------------------------------
# Re-offering: the thing UNIQUE(order_id) made impossible
# --------------------------------------------------------------------------


def test_a_released_delivery_can_be_offered_again(db):
    """Under the old schema this was unreachable: delivery_offers had
    UNIQUE(order_id), so once an order had been offered once it could never be
    offered again, and a rider who claimed and abandoned took the order with
    them."""
    from sqlalchemy import select

    from app.controllers.courier_controller import advance_delivery
    from app.models import DeliveryOffer
    from app.services.delivery_dispatch_service import open_offer

    user, courier = _courier(db)
    order = _order(db)
    delivery = _delivery(db, order, status="accepted", courier=courier)
    first = _offer(db, delivery)
    first.status = "claimed"
    db.flush()

    advance_delivery(db, user, delivery.id, "release")
    db.refresh(delivery)
    assert delivery.status == "pending_assignment"
    assert delivery.courier_id is None
    assert delivery.order.courier_id is None

    second = open_offer(db, delivery)
    db.flush()

    assert second.id != first.id
    db.refresh(delivery)
    assert delivery.status == "offered"

    offers = db.scalars(
        select(DeliveryOffer).where(DeliveryOffer.delivery_id == delivery.id)
    ).all()
    assert len(offers) == 2
    assert sum(1 for o in offers if o.status == "open") == 1


def test_only_one_open_offer_per_delivery_can_exist(db):
    """The partial unique index is what replaced UNIQUE(order_id). If it were
    dropped, two riders could claim the same delivery through two different
    open rows."""
    from sqlalchemy.exc import IntegrityError

    order = _order(db)
    delivery = _delivery(db, order, status="offered")
    _offer(db, delivery)

    with pytest.raises(IntegrityError):
        _offer(db, delivery)
        db.flush()


# --------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------


def test_location_is_rejected_without_an_active_delivery(db):
    """§20: riders are not tracked when they are not delivering."""
    from app.controllers.courier_controller import update_courier_location
    from app.schemas.courier import CourierLocationUpdate

    user, courier = _courier(db)

    with pytest.raises(HTTPException) as exc:
        update_courier_location(
            db, user, CourierLocationUpdate(latitude=5.6, longitude=-0.18)
        )
    assert exc.value.status_code == 409

    db.refresh(courier)
    assert courier.current_latitude is None


def test_location_is_accepted_while_carrying_a_delivery(db):
    from app.controllers.courier_controller import update_courier_location
    from app.schemas.courier import CourierLocationUpdate

    user, courier = _courier(db)
    _delivery(db, _order(db), status="picked_up", courier=courier)

    update_courier_location(
        db, user, CourierLocationUpdate(latitude=5.6, longitude=-0.18)
    )

    db.refresh(courier)
    assert courier.current_latitude == pytest.approx(5.6)


# --------------------------------------------------------------------------
# What the pool discloses
# --------------------------------------------------------------------------


def test_the_pool_does_not_disclose_the_address_or_order_value(db):
    from app.controllers.courier_controller import list_delivery_pool

    user, _courier_row = _courier(db)
    order = _order(db)
    delivery = _delivery(db, order, status="offered")
    _offer(db, delivery)

    entries = list_delivery_pool(db, user)
    assert len(entries) == 1
    payload = entries[0].model_dump()

    serialized = str(payload)
    assert "Independence Avenue" not in serialized
    assert "1250" not in serialized
    assert "Ama" not in serialized
    # What a rider does get: where it goes, roughly, and how long they have.
    assert payload["dropoff_area"] == "Accra, Greater Accra"
    assert payload["sla_deadline"] is not None


def test_an_open_pool_rider_cannot_see_or_claim_vendor_dedicated_work(db):
    from app.controllers.courier_controller import (
        claim_delivery_offer,
        list_delivery_pool,
    )
    from app.models import Store

    store = Store(
        id=f"store-{uuid.uuid4().hex[:8]}",
        slug=f"slug-{uuid.uuid4().hex[:8]}",
        title="Dedicated Store",
        image_key="stores/test.png",
        image_url="https://example.test/stores/test.png",
    )
    db.add(store)
    db.flush()

    order = _order(db)
    delivery = _delivery(db, order, status="offered", vendor_id=store.id)
    offer = _offer(db, delivery, vendor_id=store.id)

    open_pool_user, _ = _courier(db)  # vendor_id is None -> ODOS open pool

    assert list_delivery_pool(db, open_pool_user) == []

    with pytest.raises(HTTPException) as exc:
        claim_delivery_offer(db, open_pool_user, offer.id)
    assert exc.value.status_code == 403


def test_expired_offers_leave_the_pool_and_free_the_delivery(db):
    from app.controllers.courier_controller import list_delivery_pool
    from app.services.delivery_dispatch_service import expire_stale_offers

    user, _ = _courier(db)
    delivery = _delivery(db, _order(db), status="offered")
    offer = _offer(db, delivery, minutes=-1)

    assert list_delivery_pool(db, user) == []

    assert expire_stale_offers(db) == 1
    db.refresh(offer)
    db.refresh(delivery)
    assert offer.status == "expired"
    # Back to pending_assignment, not dead: an order must not sit unclaimed,
    # it has to become visible to admin assignment.
    assert delivery.status == "pending_assignment"


def test_a_rider_can_only_carry_one_delivery_at_a_time(db):
    from app.controllers.courier_controller import claim_delivery_offer

    user, courier = _courier(db)
    _delivery(db, _order(db), status="picked_up", courier=courier)

    second = _delivery(db, _order(db), status="offered")
    offer = _offer(db, second)

    with pytest.raises(HTTPException) as exc:
        claim_delivery_offer(db, user, offer.id)
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------
# Who an offer is aimed at
# --------------------------------------------------------------------------


def _store(db):
    from app.models import Store

    store = Store(
        id=f"store-{uuid.uuid4().hex[:8]}",
        slug=f"slug-{uuid.uuid4().hex[:8]}",
        title="Osu Electronics",
        image_key="stores/test.png",
        image_url="https://example.test/stores/test.png",
        city="Osu",
        region="Greater Accra",
    )
    db.add(store)
    db.flush()
    return store


def test_a_vendor_without_a_fleet_offers_into_the_open_pool(db):
    """Regression. Delivery.vendor_id says which store to collect from;
    DeliveryOffer.vendor_id says who may take the job. Copying the first into
    the second scopes every offer to a vendor and leaves the ODOS open pool
    permanently empty -- the same class of bug as never creating offers at all,
    only harder to spot, because the pool query still runs fine and just
    returns nothing."""
    from app.controllers.courier_controller import list_delivery_pool
    from app.services.delivery_dispatch_service import open_offer

    store = _store(db)
    delivery = _delivery(db, _order(db), status="pending_assignment", vendor_id=store.id)

    offer = open_offer(db, delivery)
    db.flush()

    assert offer.vendor_id is None, "no dedicated fleet -> the open pool"

    open_pool_user, _ = _courier(db)
    assert len(list_delivery_pool(db, open_pool_user)) == 1


def test_a_vendor_with_a_fleet_gets_first_refusal_then_it_escalates(db):
    """Hybrid fleet: a vendor's own riders see their work first. If none of
    them takes it before the SLA, it must not stall -- it escalates to the open
    pool."""
    from app.controllers.courier_controller import list_delivery_pool
    from app.services.delivery_dispatch_service import expire_stale_offers, open_offer

    store = _store(db)
    dedicated_user, _dedicated = _courier(db, vendor_id=store.id)
    open_pool_user, _ = _courier(db)

    delivery = _delivery(db, _order(db), status="pending_assignment", vendor_id=store.id)
    first = open_offer(db, delivery, sla_minutes=-1)
    db.flush()

    assert first.vendor_id == store.id
    # Visible to the vendor's rider, invisible to everyone else. (Both see an
    # empty pool right now only because this offer is already past its SLA;
    # scope is asserted on the row itself above.)
    assert list_delivery_pool(db, open_pool_user) == []

    expire_stale_offers(db)
    db.refresh(first)
    assert first.status == "expired"

    # ...and it is now in front of every rider instead of nobody.
    escalated = list_delivery_pool(db, open_pool_user)
    assert len(escalated) == 1
    assert escalated[0].delivery_id == delivery.id
    assert list_delivery_pool(db, dedicated_user) == []
