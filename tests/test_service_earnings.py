"""Provider payouts: proportionality, idempotency and refusal.

These are the paths where a bug moves real money, so each asserts a property
rather than a happy path: never pay more than was collected, never pay twice,
never pay for work that is not finished.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.service import Booking
from app.services.service_earnings import EarningsError, release_booking_earnings


class FakeWallet:
    def __init__(self):
        self.id = "wallet-1"
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
    import app.services.service_earnings as mod

    monkeypatch.setattr(mod, "_lock_vendor_wallet", lambda db, vid: w)
    return w


def make_booking(**overrides) -> Booking:
    booking = Booking(
        booking_number="BKG-200001",
        service_id="svc_1",
        store_id="store_1",
        customer_user_id=None,
        booking_type="instant",
        status=overrides.pop("status", "completed"),
        total_amount=overrides.pop("total_amount", 1000.0),
        amount_paid=overrides.pop("amount_paid", 1000.0),
        commission_amount=overrides.pop("commission_amount", 100.0),
        commission_rate=0.10,
    )
    booking.provider_user_id = overrides.pop("provider_user_id", "provider-1")
    booking.earnings_released_at = overrides.pop("earnings_released_at", None)
    for key, value in overrides.items():
        setattr(booking, key, value)
    return booking


class TestFullPayment:
    def test_credits_net_of_commission(self, wallet):
        db = FakeSession()
        booking = make_booking(total_amount=1000.0, amount_paid=1000.0, commission_amount=100.0)

        paid = release_booking_earnings(db, booking)

        assert paid == 900.0
        assert wallet.available_balance == 900.0
        assert wallet.total_commission == 100.0
        assert booking.earnings_released_at is not None

    def test_records_an_attributable_wallet_transaction(self, wallet):
        db = FakeSession()
        booking = make_booking()
        release_booking_earnings(db, booking)

        tx = db.added[0]
        assert tx.booking_id == booking.id
        assert tx.kind == "booking_earning"
        assert tx.gross_amount == 1000.0
        assert tx.commission_amount == 100.0
        assert tx.amount == 900.0


class TestPartialPayment:
    def test_deposit_only_releases_its_share(self, wallet):
        """A 30% deposit pays 30% of the job, not the whole fee."""
        db = FakeSession()
        booking = make_booking(
            total_amount=1000.0, amount_paid=300.0, commission_amount=100.0
        )

        paid = release_booking_earnings(db, booking)

        # 30% collected -> 30 commission recognised, 270 to the provider.
        assert paid == 270.0
        assert wallet.total_commission == 30.0

    def test_never_pays_more_than_was_collected(self, wallet):
        db = FakeSession()
        booking = make_booking(
            total_amount=1000.0, amount_paid=250.0, commission_amount=100.0
        )
        paid = release_booking_earnings(db, booking)
        assert paid <= booking.amount_paid

    def test_uncollected_booking_pays_nothing(self, wallet):
        """Paid in person: the platform holds no funds to release."""
        db = FakeSession()
        booking = make_booking(amount_paid=0.0)

        paid = release_booking_earnings(db, booking)

        assert paid == 0.0
        assert wallet.available_balance == 0.0
        # Still settled, so the sweeper stops reconsidering it.
        assert booking.earnings_released_at is not None


class TestIdempotency:
    def test_second_release_pays_nothing(self, wallet):
        db = FakeSession()
        booking = make_booking()

        first = release_booking_earnings(db, booking)
        second = release_booking_earnings(db, booking)

        assert first == 900.0
        assert second == 0.0
        assert wallet.available_balance == 900.0
        assert len(db.added) == 1

    def test_already_released_booking_is_untouched(self, wallet):
        db = FakeSession()
        booking = make_booking(earnings_released_at=datetime.now(timezone.utc))
        assert release_booking_earnings(db, booking) == 0.0
        assert not db.added


class TestRefusal:
    @pytest.mark.parametrize(
        "status", ["pending_provider", "pending_payment", "confirmed", "in_progress", "cancelled"]
    )
    def test_refuses_unless_completed(self, wallet, status):
        db = FakeSession()
        booking = make_booking(status=status)
        with pytest.raises(EarningsError, match="when a booking completes"):
            release_booking_earnings(db, booking)

    def test_missing_provider_holds_the_liability(self, wallet):
        """Deleted provider: funds stay in treasury rather than vanishing."""
        db = FakeSession()
        booking = make_booking(provider_user_id=None)

        assert release_booking_earnings(db, booking) == 0.0
        assert not db.added
        # Not marked released -- finance still has to resolve it.
        assert booking.earnings_released_at is None


class TestConservation:
    @pytest.mark.parametrize(
        "total,paid,commission",
        [
            (1000.0, 1000.0, 100.0),
            (1000.0, 300.0, 100.0),
            (750.0, 750.0, 0.75),
            (99.99, 50.0, 10.0),
        ],
    )
    def test_commission_plus_net_equals_collected(self, wallet, total, paid, commission):
        """No money is created or destroyed in the split."""
        db = FakeSession()
        booking = make_booking(
            total_amount=total, amount_paid=paid, commission_amount=commission
        )
        net = release_booking_earnings(db, booking)
        assert net + wallet.total_commission == pytest.approx(paid, abs=0.01)
