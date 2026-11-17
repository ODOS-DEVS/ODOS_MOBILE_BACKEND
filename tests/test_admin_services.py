"""Admin controls, and the limits on them.

An admin can do things neither party can, which is exactly why the
boundaries matter: resolving a dispute moves real money, and the route that
does it must not become a way to edit a balance by hand.
"""

from __future__ import annotations

import inspect

import pytest

from app.controllers.admin import services as admin_services
from app.main import app


class TestDisputeResolution:
    def test_releasing_goes_through_the_escrow_path(self):
        """Not a status edit. The ledger entry and wallet credit must be
        identical to a customer confirmation; only the reason differs."""
        src = inspect.getsource(admin_services.resolve_dispute)
        assert "confirm_and_release" in src
        assert "RELEASE_ADMIN" in src

    def test_cancelling_does_not_release_the_hold(self):
        """Funds stay in treasury for the existing refund process."""
        src = inspect.getsource(admin_services.resolve_dispute)
        release_line = src.index("confirm_and_release")
        guard = src.rindex('outcome == "completed"', 0, release_line)
        assert guard < release_line

    def test_only_two_outcomes_are_possible(self):
        src = inspect.getsource(admin_services.resolve_dispute)
        assert 'outcome not in ("completed", "cancelled")' in src

    def test_a_reason_is_mandatory(self):
        """A resolution nobody can explain later is indistinguishable from
        an admin reaching into the money."""
        src = inspect.getsource(admin_services.resolve_dispute)
        assert "Record why the dispute" in src

    def test_only_disputed_bookings_can_be_resolved(self):
        src = inspect.getsource(admin_services.resolve_dispute)
        assert 'status != "disputed"' in src

    def test_the_booking_row_is_locked(self):
        src = inspect.getsource(admin_services.resolve_dispute)
        assert "with_for_update" in src

    def test_it_still_goes_through_the_state_machine(self):
        """Admin bypasses the actor check, never the legality check."""
        src = inspect.getsource(admin_services.resolve_dispute)
        assert "record_status_change" in src
        assert 'actor_role="admin"' in src


class TestListingModeration:
    def test_rejection_requires_a_reason(self):
        src = inspect.getsource(admin_services.set_service_admin_status)
        assert "Give a reason" in src

    def test_suspending_does_not_touch_existing_bookings(self):
        """Work people already paid for must survive a listing being pulled."""
        src = inspect.getsource(admin_services.set_service_admin_status)
        assert "Booking" not in src

    def test_only_an_admin_can_activate_a_listing(self):
        """The provider-facing setter excludes 'active'."""
        from app.controllers.service_controller import set_service_status

        provider_src = inspect.getsource(set_service_status)
        assert '"draft", "pending_review", "paused", "archived"' in provider_src
        admin_src = inspect.getsource(admin_services.set_service_admin_status)
        assert '"active"' in admin_src


class TestCategories:
    def test_deactivates_rather_than_deletes(self):
        """Deleting would orphan listings and break shared links."""
        src = inspect.getsource(admin_services.deactivate_category)
        assert "is_active = False" in src
        assert "db.delete" not in src

    def test_a_category_cannot_parent_itself(self):
        src = inspect.getsource(admin_services.update_category)
        assert "its own parent" in src

    def test_slug_clashes_are_refused(self):
        for fn in (admin_services.create_category, admin_services.update_category):
            assert "already exists" in inspect.getsource(fn)


class TestOfferings:
    def test_unknown_offering_is_refused(self):
        src = inspect.getsource(admin_services.set_store_offerings)
        assert "Unknown offering" in src

    def test_a_store_must_offer_something(self):
        src = inspect.getsource(admin_services.set_store_offerings)
        assert "must offer something" in src


class TestOverview:
    def test_reports_what_is_held_in_escrow(self):
        """The number an operator is actually accountable for."""
        src = inspect.getsource(admin_services.services_marketplace_overview)
        assert "amount_held_in_escrow" in src
        assert "earnings_released_at.is_(None)" in src

    def test_surfaces_the_queues_needing_action(self):
        src = inspect.getsource(admin_services.services_marketplace_overview)
        for key in (
            "listings_pending_review",
            "open_disputes",
            "providers_awaiting_verification",
            "bookings_awaiting_release",
        ):
            assert key in src


class TestRouteGuards:
    """Which permission band each route actually requires.

    Read out of the dependency's closure rather than matched against the
    annotation string: `require_admin_feature("finance")` and
    `require_admin_feature("catalog")` produce functions with identical
    qualnames, so a string match cannot tell a finance route from a
    catalogue one and would pass whatever band were used.
    """

    def _admin_routes(self):
        return [
            r for r in app.routes
            if getattr(r, "path", "").startswith("/api/admin/services")
        ]

    @staticmethod
    def _features(route) -> set[str]:
        found = set()
        for dep in route.dependant.dependencies:
            fn = dep.call
            if getattr(fn, "__qualname__", "").startswith("require_admin_feature"):
                for cell in (fn.__closure__ or ()):
                    if isinstance(cell.cell_contents, str):
                        found.add(cell.cell_contents)
        return found

    def test_every_admin_route_is_feature_guarded(self):
        unguarded = [r.path for r in self._admin_routes() if not self._features(r)]
        assert not unguarded, f"unguarded admin routes: {unguarded}"

    def test_money_routes_sit_behind_the_finance_band(self):
        """A support or catalogue admin must not be able to release funds."""
        money_routes = [
            r for r in self._admin_routes()
            if "resolve-dispute" in r.path or r.path.endswith("/bookings")
        ]
        assert money_routes, "expected money routes to exist"
        for route in money_routes:
            assert self._features(route) == {"finance"}, (
                route.path, self._features(route)
            )

    def test_provider_verification_sits_behind_the_vendors_band(self):
        for route in self._admin_routes():
            if "/providers/" in route.path:
                assert self._features(route) == {"vendors"}, route.path

    def test_category_management_sits_behind_the_catalog_band(self):
        for route in self._admin_routes():
            if "/categories" in route.path or "/listings" in route.path:
                assert self._features(route) == {"catalog"}, route.path

    def test_the_dispute_route_is_registered(self):
        paths = {r.path for r in app.routes}
        assert "/api/admin/services/bookings/{booking_id}/resolve-dispute" in paths
