"""Odos holds the money until the work is confirmed.

The property under test throughout: a provider marking their own work
complete must never release funds. If it could, the hold would protect
nobody, which is the entire reason it exists.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.service import Booking
from app.services.service_escrow import (
    ESCROW_GRACE_HOURS,
    RELEASE_ADMIN,
    RELEASE_AUTO,
    RELEASE_CUSTOMER_CONFIRMED,
    EscrowError,
    confirm_and_release,
    hold_summary,
    start_hold,
)


class FakeWallet:
    def __init__(self):
        self.id = "w1"
        self.available_balance = 0.0
        self.lifetime_earnings = 0.0
        self.total_commission = 0.0


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


@pytest.fixture
def wallet(monkeypatch):
    w = FakeWallet()
    import app.services.service_earnings as earnings

    monkeypatch.setattr(earnings, "_lock_vendor_wallet", lambda db, vid: w)
    return w


def make_booking(**overrides) -> Booking:
    booking = Booking(
        booking_number="BKG-300001",
        service_id="svc_1",
        store_id="store_1",
        customer_user_id=None,
        booking_type="instant",
        status=overrides.pop("status", "completed"),
        total_amount=overrides.pop("total_amount", 500.0),
        amount_paid=overrides.pop("amount_paid", 500.0),
        commission_amount=overrides.pop("commission_amount", 50.0),
    )
    booking.provider_user_id = "provider-1"
    booking.earnings_released_at = overrides.pop("earnings_released_at", None)
    booking.work_completed_at = overrides.pop("work_completed_at", None)
    booking.customer_confirmed_at = None
    booking.auto_release_at = overrides.pop("auto_release_at", None)
    booking.earnings_release_reason = None
    for key, value in overrides.items():
        setattr(booking, key, value)
    return booking


class TestStartingTheHold:
    def test_completion_opens_a_grace_window(self):
        booking = make_booking()
        now = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)

        start_hold(booking, now=now)

        assert booking.work_completed_at == now
        assert booking.auto_release_at == now + timedelta(hours=ESCROW_GRACE_HOURS)

    def test_starting_the_hold_pays_nothing(self, wallet):
        """The provider's claim does not move money."""
        booking = make_booking()
        start_hold(booking)
        assert wallet.available_balance == 0.0
        assert booking.earnings_released_at is None


class TestCustomerConfirmation:
    def test_confirming_releases_the_funds(self, wallet):
        db = FakeSession()
        booking = make_booking(total_amount=500.0, amount_paid=500.0, commission_amount=50.0)
        start_hold(booking)

        amount = confirm_and_release(db, booking)

        assert amount == 450.0
        assert wallet.available_balance == 450.0
        assert booking.customer_confirmed_at is not None
        assert booking.earnings_release_reason == RELEASE_CUSTOMER_CONFIRMED

    def test_confirming_twice_pays_once(self, wallet):
        db = FakeSession()
        booking = make_booking()
        start_hold(booking)

        first = confirm_and_release(db, booking)
        second = confirm_and_release(db, booking)

        assert first > 0
        assert second == 0.0
        assert len(db.added) == 1

    def test_cannot_release_work_that_is_not_complete(self, wallet):
        db = FakeSession()
        for status in ("confirmed", "in_progress", "pending_payment", "cancelled"):
            booking = make_booking(status=status)
            with pytest.raises(EscrowError, match="Only completed work"):
                confirm_and_release(db, booking)
        assert wallet.available_balance == 0.0


class TestAutoRelease:
    def test_grace_elapsing_releases_to_the_provider(self, wallet):
        """A silent customer must not strand the provider's money."""
        db = FakeSession()
        booking = make_booking()
        start_hold(booking)

        amount = confirm_and_release(db, booking, reason=RELEASE_AUTO)

        assert amount == 450.0
        assert booking.earnings_release_reason == RELEASE_AUTO
        # Auto-release is not a customer confirmation and must not claim to be.
        assert booking.customer_confirmed_at is None

    def test_admin_release_is_recorded_as_such(self, wallet):
        db = FakeSession()
        booking = make_booking()
        start_hold(booking)

        confirm_and_release(db, booking, reason=RELEASE_ADMIN)

        assert booking.earnings_release_reason == RELEASE_ADMIN
        assert booking.customer_confirmed_at is None


class TestHoldSummary:
    def test_shows_money_held_while_awaiting_confirmation(self):
        booking = make_booking(amount_paid=500.0)
        start_hold(booking)

        summary = hold_summary(booking)

        assert summary["amount_held"] == 500.0
        assert summary["awaiting_confirmation"] is True
        assert summary["auto_release_at"] is not None

    def test_shows_nothing_held_once_released(self, wallet):
        db = FakeSession()
        booking = make_booking(amount_paid=500.0)
        start_hold(booking)
        confirm_and_release(db, booking)

        summary = hold_summary(booking)

        assert summary["amount_held"] == 0.0
        assert summary["awaiting_confirmation"] is False
        assert summary["released_at"] is not None


class TestEscrowInvariants:
    def test_grace_window_matches_the_delivery_promise(self):
        """Services and deliveries must not mean different things by 'soon'."""
        from app.services.delivery_lifecycle_service import AUTO_RELEASE_GRACE_HOURS

        assert ESCROW_GRACE_HOURS == AUTO_RELEASE_GRACE_HOURS

    def test_released_amount_never_exceeds_what_was_collected(self, wallet):
        db = FakeSession()
        booking = make_booking(total_amount=500.0, amount_paid=500.0, commission_amount=50.0)
        start_hold(booking)
        amount = confirm_and_release(db, booking)
        assert amount <= booking.amount_paid
