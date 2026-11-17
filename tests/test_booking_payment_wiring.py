"""The booking payment path: one gateway, one charge, held not released.

Structural rather than request-level — the integration fixtures need a live
database — but these assert the properties that would be security or money
bugs if they regressed, not merely that code exists.
"""

from __future__ import annotations

import inspect

import pytest

from app.controllers import booking_payment_controller as bpc
from app.controllers import payment_controller as pc
from app.main import app


class TestSingleGateway:
    def test_bookings_are_pinned_to_kutanapay(self):
        from app.services.service_booking_service import BOOKING_PAYMENT_PROVIDER

        assert BOOKING_PAYMENT_PROVIDER == "kutanapay"

    def test_checkout_uses_the_pinned_provider_not_a_default(self):
        """A config default must not be able to reroute service money."""
        src = inspect.getsource(bpc.create_booking_checkout)
        assert "BOOKING_PAYMENT_PROVIDER" in src
        assert "paystack" not in src.lower()

    def test_no_paystack_anywhere_in_the_booking_payment_path(self):
        src = inspect.getsource(bpc)
        assert "paystack" not in src.lower()


class TestFullPaymentOnly:
    def test_checkout_charges_the_whole_total(self):
        src = inspect.getsource(bpc.create_booking_checkout)
        assert "booking.total_amount" in src
        # No proportion of the total is ever charged.
        assert "deposit" not in src.lower()

    def test_transaction_purpose_is_always_full(self):
        src = inspect.getsource(bpc.create_booking_checkout)
        assert 'purpose="full"' in src


class TestHeldNotReleased:
    def test_settlement_books_to_treasury_but_does_not_pay_the_provider(self):
        """Payment funds the hold. Only confirmation pays out."""
        src = inspect.getsource(bpc.apply_verified_booking_payment)
        assert "record_booking_payment_collection" in src
        # The wallet credit must not happen here.
        assert "release_booking_earnings" not in src
        assert "confirm_and_release" not in src

    def test_completion_starts_a_hold_rather_than_paying(self):
        from app.controllers import service_controller as sc

        src = inspect.getsource(sc.transition_booking)
        assert "start_hold" in src
        assert "release_booking_earnings" not in src


class TestIdempotency:
    def test_settlement_short_circuits_an_already_paid_booking(self):
        """KutanaPay retries deliveries; the second must not double-book."""
        src = inspect.getsource(bpc.apply_verified_booking_payment)
        assert 'payment_status == "paid"' in src

    def test_settlement_locks_the_booking_row(self):
        """A webhook racing the customer's return must serialise."""
        src = inspect.getsource(bpc.apply_verified_booking_payment)
        assert "with_for_update" in src

    def test_checkout_reuses_a_live_session(self):
        """Two open checkouts for one booking means one becomes a refund."""
        src = inspect.getsource(bpc.create_booking_checkout)
        assert "pending" in src and "initialized" in src


class TestWebhookRouting:
    def test_webhook_routes_bookings_away_from_the_order_verifier(self):
        """The order verifier 500s without an order, so KutanaPay would
        retry a booking delivery forever."""
        src = inspect.getsource(pc.handle_kutanapay_webhook)
        assert "transaction.booking_id is not None" in src
        assert "verify_booking_payment" in src

    def test_order_verifier_contract_is_unchanged(self):
        """Bookings were routed around it, not threaded through it."""
        src = inspect.getsource(pc._verify_kutanapay_payment)
        assert "booking" not in src.lower()


class TestOwnership:
    def test_checkout_refuses_someone_elses_booking(self):
        src = inspect.getsource(bpc._load_payable_booking)
        assert "customer_user_id != user.id" in src

    def test_checkout_refuses_a_booking_not_awaiting_payment(self):
        src = inspect.getsource(bpc._load_payable_booking)
        assert 'status != "pending_payment"' in src

    def test_verify_is_scoped_to_the_calling_user(self):
        src = inspect.getsource(bpc.verify_booking_payment_for_user)
        assert "user_id == current_user.id" in src


class TestRoutes:
    def test_checkout_and_verify_are_registered(self):
        paths = {r.path for r in app.routes}
        assert "/api/bookings/{booking_id}/checkout" in paths
        assert "/api/bookings/payments/{reference}/verify" in paths
        assert "/api/bookings/{booking_id}/confirm" in paths

    def test_payment_routes_require_authentication(self):
        for route in app.routes:
            if getattr(route, "path", "") in (
                "/api/bookings/{booking_id}/checkout",
                "/api/bookings/payments/{reference}/verify",
                "/api/bookings/{booking_id}/confirm",
            ):
                assert "current_user" in inspect.signature(route.endpoint).parameters


class TestScheduling:
    def test_escrow_release_runs_in_both_schedulers(self):
        """Registered in only one, it silently never runs in the other
        deployment -- compose disables the in-process loops."""
        from app.core.celery_app import celery_app
        import app.tasks as tasks

        assert "booking-escrow-release" in celery_app.conf.beat_schedule
        assert hasattr(tasks, "booking_escrow_release")

        main_src = inspect.getsource(__import__("app.main", fromlist=["x"]))
        assert "_booking_escrow_loop" in main_src
        assert "asyncio.create_task(_booking_escrow_loop())" in main_src

    def test_maintenance_sweeps_run_in_both_schedulers(self):
        """Unpaid bookings hold slots; unswept, the calendar seizes up."""
        from app.core.celery_app import celery_app
        import app.tasks as tasks

        assert "services-maintenance" in celery_app.conf.beat_schedule
        assert hasattr(tasks, "services_maintenance")

        main_src = inspect.getsource(__import__("app.main", fromlist=["x"]))
        assert "asyncio.create_task(_services_maintenance_loop())" in main_src

    def test_maintenance_isolates_its_two_sweeps(self):
        """One failing sweep must not stop the other."""
        from app.services.service_maintenance import process_services_maintenance

        src = inspect.getsource(process_services_maintenance)
        assert src.count("except Exception") >= 2


class TestNotifications:
    @pytest.mark.parametrize(
        "hook",
        [
            "notify_booking_requested",
            "notify_booking_accepted",
            "notify_booking_declined",
            "notify_booking_confirmed",
            "notify_work_completed",
            "notify_funds_released",
            "notify_booking_cancelled",
        ],
    )
    def test_every_lifecycle_hook_exists(self, hook):
        import app.services.service_notifications as n

        assert callable(getattr(n, hook))

    def test_a_failed_notification_cannot_break_the_booking(self):
        """Both the in-app write and the push are caught."""
        import app.services.service_notifications as n

        src = inspect.getsource(n._notify)
        assert src.count("except Exception") >= 2

    def test_notifications_fire_after_the_commit(self):
        """Announced only once the state change is durable."""
        from app.controllers import service_controller as sc

        src = inspect.getsource(sc.transition_booking)
        assert src.index("db.commit()") < src.index("_announce_transition")
