"""The delivery pool claim mechanic, exercised with real concurrent
connections.

This proves the specific behaviour SKIP LOCKED gives that FOR UPDATE alone
does not: a second courier querying the same open offer at the same moment
does not wait on the first courier's transaction and then fail -- it simply
does not see the row, and gets a clean "already claimed" immediately.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from tests.conftest import TEST_DATABASE_URL, requires_db

pytestmark = requires_db


def _make_courier_with_profile(session, *, online: bool = True):
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
        user_id=user.id,
        vehicle_type="bike",
        is_online=online,
    )
    session.add(courier)
    session.flush()
    return user, courier


def _make_ready_order(session):
    from app.models import Order

    order = Order(
        order_number=f"ORD-{uuid.uuid4().hex[:10].upper()}",
        user_id=session.scalars(
            select(__import__("app.models", fromlist=["User"]).User.id)
        ).first(),
        subtotal_amount=50.0,
        total_amount=50.0,
        address_full_name="Test Buyer",
        address_phone="0200000000",
        address_street="1 Test Street",
        address_city="Accra",
        address_region="Greater Accra",
        payment_type="card",
        payment_label="Card",
    )
    session.add(order)
    session.flush()
    return order


@pytest.fixture
def make_buyer(db):
    from app.models import User

    def _make():
        user = User(
            full_name="Test Buyer",
            email=f"buyer-{uuid.uuid4().hex[:12]}@example.com",
        )
        db.add(user)
        db.flush()
        return user

    return _make


def test_claiming_an_offer_marks_it_claimed_and_assigns_the_order(
    db, make_buyer,
):
    from app.controllers.courier_controller import claim_delivery_offer
    from app.models import DeliveryOffer, Order

    buyer = make_buyer()
    order = Order(
        order_number=f"ORD-{uuid.uuid4().hex[:10].upper()}",
        user_id=buyer.id,
        subtotal_amount=50.0,
        total_amount=50.0,
        address_full_name="Test Buyer",
        address_phone="0200000000",
        address_street="1 Test Street",
        address_city="Accra",
        address_region="Greater Accra",
        payment_type="card",
        payment_label="Card",
    )
    db.add(order)
    db.flush()

    offer = DeliveryOffer(
        order_id=order.id,
        status="open",
        sla_deadline=datetime.now(UTC) + timedelta(minutes=15),
    )
    db.add(offer)
    db.flush()

    courier_user, courier = _make_courier_with_profile(db)

    result = claim_delivery_offer(db, courier_user, offer.id)

    assert result.status == "claimed"
    db.refresh(order)
    assert order.courier_id == courier.id
    assert order.courier_assigned_at is not None


def test_claiming_an_already_claimed_offer_is_rejected(db, make_buyer):
    from fastapi import HTTPException

    from app.controllers.courier_controller import claim_delivery_offer
    from app.models import DeliveryOffer, Order

    buyer = make_buyer()
    order = Order(
        order_number=f"ORD-{uuid.uuid4().hex[:10].upper()}",
        user_id=buyer.id,
        subtotal_amount=50.0,
        total_amount=50.0,
        address_full_name="Test Buyer",
        address_phone="0200000000",
        address_street="1 Test Street",
        address_city="Accra",
        address_region="Greater Accra",
        payment_type="card",
        payment_label="Card",
    )
    db.add(order)
    db.flush()

    offer = DeliveryOffer(
        order_id=order.id,
        status="claimed",
        claimed_at=datetime.now(UTC),
        sla_deadline=datetime.now(UTC) + timedelta(minutes=15),
    )
    db.add(offer)
    db.flush()

    _, courier = _make_courier_with_profile(db)
    _2, courier2 = _make_courier_with_profile(db)

    with pytest.raises(HTTPException) as exc_info:
        claim_delivery_offer(db, _2, offer.id)
    assert exc_info.value.status_code == 409


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="needs a real database")
def test_two_couriers_claiming_the_same_offer_concurrently_exactly_one_wins():
    """The failure this prevents: two couriers tap the same delivery at the
    same moment, and without SKIP LOCKED, both could be told they got it --
    or one hangs waiting on the other instead of seeing it as already gone.
    """
    from app.models import Courier, DeliveryOffer, Order, User

    engine = create_engine(TEST_DATABASE_URL, future=True)
    with Session(engine) as setup:
        buyer = User(full_name="Buyer", email=f"buyer-{uuid.uuid4().hex[:12]}@example.com")
        setup.add(buyer)
        setup.flush()

        order = Order(
            order_number=f"ORD-{uuid.uuid4().hex[:10].upper()}",
            user_id=buyer.id,
            subtotal_amount=50.0,
            total_amount=50.0,
            address_full_name="Buyer",
            address_phone="0200000000",
            address_street="1 Test Street",
            address_city="Accra",
            address_region="Greater Accra",
            payment_type="card",
            payment_label="Card",
        )
        setup.add(order)
        setup.flush()

        offer = DeliveryOffer(
            order_id=order.id,
            status="open",
            sla_deadline=datetime.now(UTC) + timedelta(minutes=15),
        )
        setup.add(offer)
        setup.flush()
        offer_id = offer.id

        courier_ids = []
        for _ in range(2):
            cu = User(
                full_name="Courier",
                email=f"courier-{uuid.uuid4().hex[:12]}@example.com",
                role="courier",
                courier_status="approved",
            )
            setup.add(cu)
            setup.flush()
            c = Courier(user_id=cu.id, vehicle_type="bike", is_online=True)
            setup.add(c)
            setup.flush()
            courier_ids.append(cu.id)

        setup.commit()

    results: list[str] = []
    barrier = threading.Barrier(2)

    def attempt_claim(user_id) -> None:
        from app.controllers.courier_controller import claim_delivery_offer
        from fastapi import HTTPException

        worker_engine = create_engine(TEST_DATABASE_URL, future=True)
        with Session(worker_engine) as session:
            user = session.get(User, user_id)
            try:
                barrier.wait(timeout=10)
                claim_delivery_offer(session, user, offer_id)
                results.append("claimed")
            except HTTPException as exc:
                results.append(f"rejected:{exc.status_code}")
            except Exception as exc:  # noqa: BLE001
                results.append(f"error:{exc.__class__.__name__}")

    threads = [threading.Thread(target=attempt_claim, args=(cid,)) for cid in courier_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert results.count("claimed") == 1, results
    assert results.count("rejected:409") == 1, results

    with Session(engine) as check:
        final_offer = check.get(DeliveryOffer, offer_id)
        assert final_offer.status == "claimed"
        assert final_offer.claimed_by_courier_id is not None

        cleanup_order_id = final_offer.order_id
        check.execute(text("DELETE FROM delivery_offers WHERE id = :i"), {"i": offer_id})
        check.execute(text("DELETE FROM orders WHERE id = :i"), {"i": cleanup_order_id})
        for cid in courier_ids:
            check.execute(text("DELETE FROM couriers WHERE user_id = :u"), {"u": cid})
            check.execute(text("DELETE FROM users WHERE id = :u"), {"u": cid})
        check.commit()
