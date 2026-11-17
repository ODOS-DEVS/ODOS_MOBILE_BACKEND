"""Booking creation decisions that do not need a database.

Covers the branches a customer can reach by sending an unusual combination:
which status a new booking starts in, when a location is demanded, and how a
deposit payment moves a booking without marking it fully paid.
"""

from __future__ import annotations

import pytest

from app.models.service import Booking, Service
from app.services.service_booking_service import (
    BookingError,
    BookingRequest,
    _initial_status,
    _validate_location,
    mark_paid,
)
from app.services.service_pricing import quote_booking


def make_service(**overrides) -> Service:
    service = Service(
        id="svc_1",
        slug="cut",
        store_id="store_1",
        title="Haircut",
        pricing_model=overrides.pop("pricing_model", "fixed"),
        booking_type=overrides.pop("booking_type", "instant"),
        payment_timing=overrides.pop("payment_timing", "upfront"),
        base_price=overrides.pop("base_price", 60.0),
        currency="GHS",
        status="active",
        duration_minutes=overrides.pop("duration_minutes", 30),
        buffer_minutes=0,
    )
    service.location_modes = overrides.pop("location_modes", None)
    for key, value in overrides.items():
        setattr(service, key, value)
    service.packages = []
    service.addons = []
    return service


def make_booking(**overrides) -> Booking:
    booking = Booking(
        booking_number="BKG-100001",
        service_id="svc_1",
        store_id="store_1",
        customer_user_id=None,
        status=overrides.pop("status", "pending_payment"),
        booking_type="instant",
        total_amount=overrides.pop("total_amount", 1000.0),
        deposit_amount=overrides.pop("deposit_amount", 300.0),
        amount_paid=overrides.pop("amount_paid", 0.0),
        payment_status="unpaid",
    )
    for key, value in overrides.items():
        setattr(booking, key, value)
    return booking


class TestInitialStatus:
    def test_request_service_waits_for_the_provider(self):
        """Money must not be taken before the provider has agreed."""
        service = make_service(booking_type="request", payment_timing="upfront")
        quote = quote_booking(service)
        assert _initial_status(service, quote) == "pending_provider"

    def test_instant_paid_service_waits_for_payment(self):
        service = make_service(booking_type="instant", payment_timing="upfront")
        assert _initial_status(service, quote_booking(service)) == "pending_payment"

    def test_free_instant_service_is_confirmed_without_payment(self):
        """Only a free service skips payment now -- 'pay later' is gone,
        because Odos holds the full amount instead."""
        service = make_service(
            booking_type="instant",
            payment_timing="free",
            pricing_model="consultation",
            base_price=0.0,
        )
        assert _initial_status(service, quote_booking(service)) == "confirmed"

    def test_paid_instant_service_always_waits_for_the_full_amount(self):
        service = make_service(booking_type="instant", payment_timing="upfront")
        quote = quote_booking(service)
        assert quote.due_now == quote.total_amount
        assert _initial_status(service, quote) == "pending_payment"

    def test_quote_services_cannot_be_booked_directly(self):
        service = make_service(
            booking_type="quote", pricing_model="quote", payment_timing="free"
        )
        with pytest.raises(BookingError, match="not booked directly"):
            _initial_status(service, quote_booking(service))


class TestLocationValidation:
    def test_address_required_when_the_provider_travels(self):
        service = make_service(location_modes=["customer"])
        request = BookingRequest(
            service_id="svc_1", customer_user_id=None, location_mode="customer"
        )
        with pytest.raises(BookingError, match="address"):
            _validate_location(service, request)

    def test_remote_service_needs_no_address(self):
        """An online tutor has no use for the customer's street."""
        service = make_service(location_modes=["remote"])
        request = BookingRequest(
            service_id="svc_1", customer_user_id=None, location_mode="remote"
        )
        _validate_location(service, request)

    def test_unsupported_mode_is_refused(self):
        service = make_service(location_modes=["provider"])
        request = BookingRequest(
            service_id="svc_1", customer_user_id=None, location_mode="customer"
        )
        with pytest.raises(BookingError, match="does not offer"):
            _validate_location(service, request)

    def test_mode_required_when_the_service_declares_options(self):
        service = make_service(location_modes=["provider", "customer"])
        request = BookingRequest(service_id="svc_1", customer_user_id=None)
        with pytest.raises(BookingError, match="Choose where"):
            _validate_location(service, request)


class TestPaymentApplication:
    def test_underpayment_does_not_confirm(self, monkeypatch):
        """Anything short of the full price leaves the slot unconfirmed.

        Replaces a deposit test: part-payment is no longer a state the
        system can be in, so the only question is paid or not.
        """
        booking = make_booking(total_amount=1000.0)
        called = []
        import app.services.service_booking_service as mod

        monkeypatch.setattr(
            mod, "record_status_change", lambda db, b, **kw: called.append(kw)
        )
        mark_paid(None, booking, amount=300.0)

        assert not called
        assert booking.status == "pending_payment"
        assert booking.payment_status != "paid"

    def test_full_payment_marks_paid(self, monkeypatch):
        booking = make_booking(total_amount=1000.0, deposit_amount=0.0)
        import app.services.service_booking_service as mod

        monkeypatch.setattr(mod, "record_status_change", lambda db, b, **kw: None)
        mark_paid(None, booking, amount=1000.0)
        assert booking.payment_status == "paid"

    def test_rounding_tolerance_still_confirms(self, monkeypatch):
        """A pesewa short from gateway rounding must not strand a booking."""
        booking = make_booking(total_amount=1000.0)
        called = []
        import app.services.service_booking_service as mod

        monkeypatch.setattr(
            mod, "record_status_change", lambda db, b, **kw: called.append(kw)
        )
        mark_paid(None, booking, amount=999.995)
        assert called, "a rounding-level shortfall should still confirm"
        assert booking.payment_status == "paid"
