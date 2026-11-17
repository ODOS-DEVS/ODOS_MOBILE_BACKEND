"""The services API's contract and its access boundaries.

Schema-level rather than request-level: the integration fixtures need a live
database and skip without one, but the contract these assert — that no
endpoint accepts a price, and that every mutating route demands
authentication — is exactly the part worth catching in CI, because a
regression there is a security bug rather than a broken screen.
"""

from __future__ import annotations

import pytest

from app.main import app
from app.schemas.service import (
    AddonSelectionWrite,
    BookingCreate,
    BookingQuoteRequest,
    ServiceWrite,
)

MONEY_FIELDS = {
    "price",
    "total",
    "total_amount",
    "amount",
    "amount_paid",
    "commission",
    "commission_amount",
    "commission_rate",
    "provider_net_amount",
    "deposit_amount",
    "due_now",
}


class TestNoClientSuppliedMoney:
    """The client picks what it wants, never what it pays."""

    @pytest.mark.parametrize(
        "model", [BookingCreate, BookingQuoteRequest, AddonSelectionWrite]
    )
    def test_booking_inputs_accept_no_monetary_field(self, model):
        leaked = MONEY_FIELDS & set(model.model_fields)
        assert not leaked, f"{model.__name__} would accept {leaked} from the client"

    def test_booking_input_rejects_an_injected_total(self):
        """Extra keys must not silently become attributes."""
        booking = BookingCreate.model_validate(
            {"addons": [], "total_amount": 1, "commission_rate": 0}
        )
        assert not hasattr(booking, "total_amount")
        assert not hasattr(booking, "commission_rate")

    def test_service_write_cannot_set_status(self):
        """A provider must not publish straight into the public catalogue."""
        assert "status" not in ServiceWrite.model_fields
        written = ServiceWrite.model_validate({"title": "Haircut", "status": "active"})
        assert not hasattr(written, "status")


class TestValidation:
    def test_rejects_an_unknown_pricing_model(self):
        with pytest.raises(ValueError, match="Pricing model"):
            ServiceWrite.model_validate({"title": "Valid title", "pricing_model": "barter"})

    def test_rejects_an_unknown_booking_type(self):
        with pytest.raises(ValueError, match="Booking type"):
            ServiceWrite.model_validate({"title": "Valid title", "booking_type": "telepathy"})

    def test_rejects_an_unknown_location_mode(self):
        with pytest.raises(ValueError, match="Unknown location"):
            ServiceWrite.model_validate({"title": "Valid title", "location_modes": ["moon"]})

    def test_rejects_a_negative_price(self):
        with pytest.raises(ValueError, match="base_price"):
            ServiceWrite.model_validate({"title": "Valid title", "base_price": -1})

    def test_deposit_percent_cannot_be_configured_at_all(self):
        """Deposits were removed; Odos takes the full amount and holds it."""
        assert "deposit_percent" not in ServiceWrite.model_fields
        written = ServiceWrite.model_validate(
            {"title": "Valid title", "deposit_percent": 50}
        )
        assert not hasattr(written, "deposit_percent")

    def test_rejects_zero_duration(self):
        with pytest.raises(ValueError, match="duration_minutes"):
            ServiceWrite.model_validate({"title": "Valid title", "duration_minutes": 0})


class TestPaymentModel:
    """One gateway, one payment, held by Odos."""

    def test_only_upfront_and_free_remain(self):
        from app.models.service import PAYMENT_TIMINGS

        assert set(PAYMENT_TIMINGS) == {"upfront", "free"}

    def test_service_write_rejects_a_deposit_timing(self):
        with pytest.raises(ValueError, match="Payment timing"):
            ServiceWrite.model_validate(
                {"title": "Valid title", "payment_timing": "deposit"}
            )

    def test_bookings_are_pinned_to_kutanapay(self):
        from app.services.service_booking_service import BOOKING_PAYMENT_PROVIDER

        assert BOOKING_PAYMENT_PROVIDER == "kutanapay"


class TestRouteSurface:
    """Every mutating endpoint sits behind authentication."""

    def _routes(self):
        return [
            r
            for r in app.routes
            if getattr(r, "path", "").startswith(("/api/services", "/api/bookings", "/api/vendor/services"))
        ]

    #: Reads that use POST because they take a structured body. Verified
    #: read-only, and public on purpose: a shopper must be able to see what a
    #: configuration costs before signing in, exactly as product prices are
    #: visible to anyone. Anything added here needs the same justification.
    PUBLIC_POST_READS = {"/api/services/{service_id}/quote"}

    def test_all_write_routes_require_a_user(self):
        unguarded = []
        for route in self._routes():
            methods = getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
            if not methods & {"POST", "PATCH", "PUT", "DELETE"}:
                continue
            if route.path in self.PUBLIC_POST_READS:
                continue
            # The dependant tree names get_current_user when the route
            # depends on it, directly or transitively.
            names = {
                getattr(d.call, "__name__", "")
                for d in route.dependant.dependencies
            }
            if "get_current_user" not in names and "current_user" not in str(
                route.dependant.path_params
            ):
                # Fall back to inspecting the signature, which is how the
                # Annotated[...] dependency actually arrives.
                import inspect

                sig = inspect.signature(route.endpoint)
                if "current_user" not in sig.parameters:
                    unguarded.append(f"{sorted(methods)} {route.path}")
        assert not unguarded, f"unauthenticated write routes: {unguarded}"

    def test_public_reads_exist_and_need_no_auth(self):
        import inspect

        public = []
        for route in self._routes():
            methods = getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
            if methods != {"GET"}:
                continue
            if route.path.startswith("/api/vendor"):
                continue
            if "current_user" not in inspect.signature(route.endpoint).parameters:
                public.append(route.path)
        # Discovery must work signed-out, or the marketplace cannot be browsed.
        assert "/api/services" in public
        assert "/api/services/categories" in public

    def test_every_vendor_route_requires_a_user(self):
        import inspect

        for route in self._routes():
            if not route.path.startswith("/api/vendor"):
                continue
            sig = inspect.signature(route.endpoint)
            assert "current_user" in sig.parameters, route.path


class TestQuoteEndpointStaysReadOnly:
    """The one unauthenticated POST must never gain a write.

    It is exempted from the auth rule above on the grounds that it only
    computes a price. If that stops being true the exemption is a hole, so
    the property is asserted rather than trusted.
    """

    def test_quote_controller_performs_no_writes(self):
        import inspect

        from app.controllers.service_controller import quote_service

        source = inspect.getsource(quote_service)
        for mutation in ("db.add", "db.commit", "db.delete", "db.flush"):
            assert mutation not in source, f"quote_service now calls {mutation}"
