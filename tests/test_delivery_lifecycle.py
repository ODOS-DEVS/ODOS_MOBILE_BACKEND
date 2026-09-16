"""Unit tests for the delivery lifecycle's static business rules: the vendor
transition table (Invariant 2 — a vendor can never reach "delivered"), the
auto-release eligibility predicate (Invariant 9 — never bypasses an active
exception), and the admin-override reason requirement.

Stateful flows (dispatch -> confirm -> settle, idempotency, the DB-level
settlement uniqueness constraint, reschedule/redispatch, problem-report,
auto-release under a row lock) need a live database session and are
exercised directly against a real Postgres instance rather than mocked here
— mirroring this test suite's existing convention of unit-testing pure
validation/derivation logic rather than standing up DB-backed integration
tests (see test_promotion_service.py, test_campaign_service.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.controllers.vendor_controller import (
    VENDOR_ALLOWED_STATUSES,
    VENDOR_STATUS_CANCELLABLE_FROM,
    VENDOR_STATUS_FORWARD_TRANSITIONS,
)
from app.services.delivery_lifecycle_service import (
    DeliveryError,
    admin_override_deliver,
    is_eligible_for_auto_release,
)


def _pair(order_overrides=None, package_overrides=None):
    """An (order, package) pair for the eligibility predicate.

    The predicate now checks both halves: the *order* must still be live and
    paid, and the *package* — one vendor's bag — must still be out for delivery
    with an elapsed clock. Each bag carries its own clock because each is
    dispatched separately, so a customer who receives one shop's items on
    Monday and another's on Thursday gets a full grace window for each.
    """
    now = datetime.now(timezone.utc)
    order = {"status": "processing", "payment_status": "paid"}
    order.update(order_overrides or {})
    package = {
        "delivery_status": "out_for_delivery",
        "vendor_status": "out_for_delivery",
        "auto_release_at": now - timedelta(hours=1),
    }
    package.update(package_overrides or {})
    return SimpleNamespace(**order), SimpleNamespace(**package)


# --- Invariant 2: a vendor can never reach "delivered" ---


def test_vendor_allowed_statuses_excludes_delivered():
    assert "delivered" not in VENDOR_ALLOWED_STATUSES


def test_vendor_forward_transitions_never_produce_delivered():
    assert "delivered" not in VENDOR_STATUS_FORWARD_TRANSITIONS.values()


def test_vendor_forward_transitions_stop_at_out_for_delivery():
    assert VENDOR_STATUS_FORWARD_TRANSITIONS == {
        "pending": "confirmed",
        "confirmed": "processing",
        "processing": "ready",
        "ready": "out_for_delivery",
    }


def test_vendor_can_only_cancel_before_prep_starts():
    assert VENDOR_STATUS_CANCELLABLE_FROM == {"pending", "confirmed"}


# --- Invariant 9: auto-release never bypasses an active exception ---


def test_auto_release_eligible_when_grace_window_elapsed():
    order, package = _pair()
    assert is_eligible_for_auto_release(order, package, datetime.now(timezone.utc)) is True


def test_auto_release_blocked_by_active_customer_problem():
    order, package = _pair(package_overrides={"delivery_status": "customer_problem"})
    assert is_eligible_for_auto_release(order, package, datetime.now(timezone.utc)) is False


def test_auto_release_blocked_while_rescheduled():
    order, package = _pair(package_overrides={"delivery_status": "rescheduled"})
    assert is_eligible_for_auto_release(order, package, datetime.now(timezone.utc)) is False


def test_auto_release_blocked_for_cancelled_order():
    order, package = _pair(order_overrides={"status": "cancelled"})
    assert is_eligible_for_auto_release(order, package, datetime.now(timezone.utc)) is False


def test_auto_release_blocked_for_cancelled_package():
    """One vendor withdrawing their items must not drag the rest of a shared
    order's money out with them."""
    order, package = _pair(package_overrides={"vendor_status": "cancelled"})
    assert is_eligible_for_auto_release(order, package, datetime.now(timezone.utc)) is False


def test_auto_release_blocked_for_already_delivered_order():
    order, package = _pair(
        order_overrides={"status": "delivered"},
        package_overrides={"delivery_status": "delivered", "vendor_status": "delivered"},
    )
    assert is_eligible_for_auto_release(order, package, datetime.now(timezone.utc)) is False


def test_auto_release_blocked_when_unpaid():
    order, package = _pair(order_overrides={"payment_status": "pending"})
    assert is_eligible_for_auto_release(order, package, datetime.now(timezone.utc)) is False


def test_auto_release_blocked_before_grace_window_elapses():
    order, package = _pair(
        package_overrides={"auto_release_at": datetime.now(timezone.utc) + timedelta(hours=1)}
    )
    assert is_eligible_for_auto_release(order, package, datetime.now(timezone.utc)) is False


def test_auto_release_blocked_when_never_scheduled():
    order, package = _pair(package_overrides={"auto_release_at": None})
    assert is_eligible_for_auto_release(order, package, datetime.now(timezone.utc)) is False


# --- Admin override always requires a reason (checked before any DB access,
# so this is reachable without a session) ---


def test_admin_override_rejects_missing_reason():
    with pytest.raises(DeliveryError) as exc_info:
        admin_override_deliver(None, SimpleNamespace(id="admin-1"), "order-1", reason="")
    assert exc_info.value.code == "ADMIN_REASON_REQUIRED"
    assert exc_info.value.status_code == 400


def test_admin_override_rejects_whitespace_only_reason():
    with pytest.raises(DeliveryError) as exc_info:
        admin_override_deliver(None, SimpleNamespace(id="admin-1"), "order-1", reason="   ")
    assert exc_info.value.code == "ADMIN_REASON_REQUIRED"


def test_delivery_error_carries_stable_code_and_status():
    error = DeliveryError("SOME_CODE", "Some detail", status_code=404)
    assert error.code == "SOME_CODE"
    assert error.detail == "Some detail"
    assert error.status_code == 404
