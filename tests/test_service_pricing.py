"""Pricing is computed from the database, never accepted from the client.

These exercise the money paths that decide what a customer is charged and
what a provider is owed, including the ones a hostile client would aim at:
an add-on from another listing, a quantity above the cap, a package on a
service that does not sell packages.

Pure unit tests over detached model instances — no database needed, which is
why they run in CI where the integration suite skips.
"""

from __future__ import annotations

import pytest

from app.models.service import Service, ServiceAddon, ServicePackage
from app.services.service_pricing import (
    AddonSelection,
    PricingError,
    quote_booking,
)


def make_service(**overrides) -> Service:
    service = Service(
        id="svc_1",
        slug="studio-portrait",
        store_id="store_1",
        title="Studio portrait",
        pricing_model=overrides.pop("pricing_model", "fixed"),
        booking_type=overrides.pop("booking_type", "instant"),
        payment_timing=overrides.pop("payment_timing", "upfront"),
        base_price=overrides.pop("base_price", 200.0),
        currency="GHS",
        status=overrides.pop("status", "active"),
        buffer_minutes=overrides.pop("buffer_minutes", 0),
        duration_minutes=overrides.pop("duration_minutes", 60),
    )
    for key, value in overrides.items():
        setattr(service, key, value)
    service.packages = overrides.get("packages", [])
    service.addons = overrides.get("addons", [])
    return service


def make_addon(**overrides) -> ServiceAddon:
    addon = ServiceAddon(
        slug=overrides.pop("slug", "extra-hour"),
        title=overrides.pop("title", "Extra hour"),
        price=overrides.pop("price", 50.0),
        duration_minutes=overrides.pop("duration_minutes", 60),
        max_quantity=overrides.pop("max_quantity", 3),
        is_active=overrides.pop("is_active", True),
    )
    addon.id = overrides.pop("id", "addon-1")
    return addon


def make_package(**overrides) -> ServicePackage:
    package = ServicePackage(
        slug=overrides.pop("slug", "premium"),
        title=overrides.pop("title", "Premium"),
        price=overrides.pop("price", 1000.0),
        duration_minutes=overrides.pop("duration_minutes", 480),
        is_active=overrides.pop("is_active", True),
    )
    package.id = overrides.pop("id", "pkg-1")
    return package


class TestFixedPricing:
    def test_charges_the_listed_price(self):
        quote = quote_booking(make_service(base_price=250.0))
        assert quote.total_amount == 250.0
        assert quote.due_now == 250.0

    def test_unpublished_service_cannot_be_priced(self):
        with pytest.raises(PricingError, match="not currently bookable"):
            quote_booking(make_service(status="draft"))


class TestHourlyPricing:
    def test_multiplies_rate_by_hours(self):
        service = make_service(pricing_model="hourly", base_price=40.0)
        quote = quote_booking(service, hours=2.5)
        assert quote.total_amount == 100.0
        assert quote.duration_minutes == 150

    def test_hours_are_required(self):
        service = make_service(pricing_model="hourly", base_price=40.0)
        with pytest.raises(PricingError, match="how many hours"):
            quote_booking(service)

    def test_rejects_sub_half_hour_precision(self):
        """Minute-level billing is a dispute generator, so it is refused."""
        service = make_service(pricing_model="hourly", base_price=40.0)
        with pytest.raises(PricingError, match="half-hour"):
            quote_booking(service, hours=1.2)


class TestPackagePricing:
    def test_uses_the_package_price_and_duration(self):
        package = make_package(price=1200.0, duration_minutes=300)
        service = make_service(pricing_model="package", packages=[package], duration_minutes=60)
        quote = quote_booking(service, package_id="pkg-1")
        assert quote.total_amount == 1200.0
        # Package duration overrides the listing default.
        assert quote.duration_minutes == 300

    def test_package_is_required(self):
        service = make_service(pricing_model="package", packages=[make_package()])
        with pytest.raises(PricingError, match="Choose a package"):
            quote_booking(service)

    def test_rejects_a_package_from_another_service(self):
        service = make_service(pricing_model="package", packages=[make_package(id="pkg-1")])
        with pytest.raises(PricingError, match="does not belong"):
            quote_booking(service, package_id="pkg-someone-else")

    def test_rejects_a_retired_package(self):
        service = make_service(
            pricing_model="package", packages=[make_package(is_active=False)]
        )
        with pytest.raises(PricingError, match="no longer offered"):
            quote_booking(service, package_id="pkg-1")

    def test_package_rejected_on_a_non_package_service(self):
        with pytest.raises(PricingError, match="not sold as packages"):
            quote_booking(make_service(), package_id="pkg-1")


class TestAddons:
    def test_extends_price_and_duration(self):
        service = make_service(base_price=200.0, addons=[make_addon(price=50.0)])
        quote = quote_booking(service, addons=[AddonSelection("addon-1", 2)])
        assert quote.addons_amount == 100.0
        assert quote.total_amount == 300.0
        # Two extra hours must push the appointment out, or the next
        # customer gets booked over the overrun.
        assert quote.duration_minutes == 60 + 120

    def test_enforces_the_quantity_cap(self):
        service = make_service(addons=[make_addon(max_quantity=2)])
        with pytest.raises(PricingError, match="limited to 2"):
            quote_booking(service, addons=[AddonSelection("addon-1", 3)])

    def test_rejects_an_addon_from_another_service(self):
        service = make_service(addons=[make_addon(id="addon-1")])
        with pytest.raises(PricingError, match="does not belong"):
            quote_booking(service, addons=[AddonSelection("addon-elsewhere", 1)])

    def test_rejects_duplicate_selections(self):
        service = make_service(addons=[make_addon()])
        with pytest.raises(PricingError, match="selected twice"):
            quote_booking(
                service, addons=[AddonSelection("addon-1", 1), AddonSelection("addon-1", 1)]
            )

    def test_rejects_an_inactive_addon(self):
        service = make_service(addons=[make_addon(is_active=False)])
        with pytest.raises(PricingError, match="no longer available"):
            quote_booking(service, addons=[AddonSelection("addon-1", 1)])


class TestFullPaymentAndEscrow:
    """No deposits: the whole price is taken and held.

    These replace an earlier deposit suite. The protection a deposit used to
    give the customer now comes from Odos holding the money until the work is
    confirmed, so there is no part-payment to compute and no balance to chase.
    """

    def test_whole_price_is_due_at_booking(self):
        service = make_service(payment_timing="upfront", base_price=1000.0)
        quote = quote_booking(service)
        assert quote.total_amount == 1000.0
        assert quote.due_now == 1000.0

    def test_addons_are_included_in_what_is_held(self):
        service = make_service(
            payment_timing="upfront", base_price=200.0, addons=[make_addon(price=100.0)]
        )
        quote = quote_booking(service, addons=[AddonSelection("addon-1", 1)])
        assert quote.total_amount == 300.0
        assert quote.due_now == 300.0

    def test_no_booking_ever_asks_for_a_partial_amount(self):
        """due_now is all of it or none of it -- never in between."""
        for price in (1.0, 99.99, 250.0, 10_000.0):
            service = make_service(payment_timing="upfront", base_price=price)
            quote = quote_booking(service)
            assert quote.due_now in (0.0, quote.total_amount)

    def test_deposit_amount_is_always_zero(self):
        service = make_service(payment_timing="upfront", base_price=1000.0)
        assert quote_booking(service).deposit_amount == 0.0

    def test_free_consultation_takes_nothing(self):
        service = make_service(
            pricing_model="consultation", payment_timing="free", base_price=0.0
        )
        quote = quote_booking(service)
        assert quote.total_amount == 0.0
        assert quote.due_now == 0.0
        assert quote.requires_payment_to_confirm is False


class TestQuoteModel:
    def test_quote_service_has_no_price_until_quoted(self):
        service = make_service(
            pricing_model="quote", payment_timing="on_completion", base_price=0.0
        )
        quote = quote_booking(service)
        assert quote.total_amount == 0.0


class TestCommission:
    def test_splits_total_into_commission_and_provider_net(self):
        service = make_service(base_price=1000.0)
        quote = quote_booking(service, commission_rate=0.10)
        assert quote.commission_amount == 100.0
        assert quote.provider_net_amount == 900.0
        # The split must account for every pesewa of the total.
        assert quote.commission_amount + quote.provider_net_amount == quote.total_amount

    def test_commission_applies_to_addons_too(self):
        service = make_service(base_price=100.0, addons=[make_addon(price=100.0)])
        quote = quote_booking(
            service, addons=[AddonSelection("addon-1", 1)], commission_rate=0.10
        )
        assert quote.total_amount == 200.0
        assert quote.commission_amount == 20.0


class TestBufferTime:
    def test_buffer_extends_the_slot_but_not_the_price(self):
        service = make_service(base_price=100.0, duration_minutes=30, buffer_minutes=15)
        quote = quote_booking(service)
        assert quote.total_amount == 100.0
        assert quote.duration_minutes == 45

    def test_no_buffer_added_to_a_service_with_no_duration(self):
        """Quote and consultation bookings hold no slot, so nothing to pad."""
        service = make_service(
            pricing_model="quote", duration_minutes=None, buffer_minutes=15
        )
        assert quote_booking(service).duration_minutes == 0
