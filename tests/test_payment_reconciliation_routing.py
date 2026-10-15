"""The stuck-payment loop must ask each gateway about its own transactions.

This is regression cover for a bug that was invisible in production logs
because it failed closed. Every pending PaymentTransaction was verified against
Paystack's ``/transaction/verify``, KutanaPay rows included. Those are keyed on
an ``odos-<uuid>`` reference Paystack has never issued, so Paystack answered
``status: false``, the service layer raised, and the loop's blanket
``except Exception`` logged it and moved on.

Nothing was corrupted -- which is why it went unnoticed -- but KutanaPay
payments had no safety net at all. Since KutanaPay is the live collector, a
single missed webhook meant a customer charged with an order that stayed
inactive until they reopened the app, and a traceback every five minutes for a
day. The tests below pin the routing rather than the logging: the point is that
a KutanaPay row reaches KutanaPay.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services import payment_reconciliation_service as svc


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    """Just enough Session to drive _process_stuck_order_payments."""

    def __init__(self, rows):
        self._rows = rows
        self.rollbacks = 0

    def scalars(self, _statement):
        return _FakeScalars(self._rows)

    def rollback(self):
        self.rollbacks += 1


class _FakeTransaction:
    def __init__(self, provider, reference, order=object()):
        self.provider = provider
        self.reference = reference
        self.order = order


@pytest.fixture
def routed(monkeypatch):
    """Record which gateway each transaction was sent to."""
    calls: dict[str, list[str]] = {"kutanapay": [], "paystack": []}

    def fake_verify_kutanapay(_db, transaction):
        calls["kutanapay"].append(transaction.reference)

    def fake_verify_paystack(reference):
        calls["paystack"].append(reference)
        return {"data": {"status": "success"}}

    import app.controllers.payment_controller as controller

    monkeypatch.setattr(
        controller, "_verify_kutanapay_payment", fake_verify_kutanapay
    )
    monkeypatch.setattr(
        controller, "_reconcile_payment_transaction", lambda *_a, **_k: None
    )
    monkeypatch.setattr(svc, "verify_transaction", fake_verify_paystack)
    return calls


def _run(rows, routed_calls=None):
    db = _FakeSession(rows)
    svc._process_stuck_order_payments(db, datetime.now(UTC))
    return db


def test_kutanapay_row_goes_to_kutanapay_not_paystack(routed):
    _run([_FakeTransaction("kutanapay", "odos-abc123")])
    assert routed["kutanapay"] == ["odos-abc123"]
    assert routed["paystack"] == [], (
        "a KutanaPay reference sent to Paystack is the original bug"
    )


def test_paystack_row_still_goes_to_paystack(routed):
    _run([_FakeTransaction("paystack", "ps-ref-1")])
    assert routed["paystack"] == ["ps-ref-1"]
    assert routed["kutanapay"] == []


def test_each_provider_is_asked_about_only_its_own_rows(routed):
    _run(
        [
            _FakeTransaction("kutanapay", "odos-1"),
            _FakeTransaction("paystack", "ps-1"),
            _FakeTransaction("kutanapay", "odos-2"),
        ]
    )
    assert routed["kutanapay"] == ["odos-1", "odos-2"]
    assert routed["paystack"] == ["ps-1"]


@pytest.mark.parametrize("provider", ["ipay", "wallet", "", None, "Unknown"])
def test_unrecognised_providers_are_left_alone(routed, provider):
    """Asking a current gateway about a retired one's rows returns "not found",
    which reads as a failure -- and would tell a customer a completed payment
    had not worked. iPay rows are still in the table."""
    _run([_FakeTransaction(provider, "legacy-ref")])
    assert routed["kutanapay"] == []
    assert routed["paystack"] == []


def test_provider_casing_and_padding_do_not_bypass_routing(routed):
    _run([_FakeTransaction("  KutanaPay  ", "odos-mixed-case")])
    assert routed["kutanapay"] == ["odos-mixed-case"]


def test_a_row_with_no_order_is_skipped(routed):
    _run([_FakeTransaction("kutanapay", "odos-orphan", order=None)])
    assert routed["kutanapay"] == []


def test_one_failing_row_does_not_stop_the_rest(monkeypatch, routed):
    """The loop runs unattended; a single bad row must not strand the others."""
    seen: list[str] = []

    def exploding_verify(_db, transaction):
        seen.append(transaction.reference)
        if transaction.reference == "odos-boom":
            raise RuntimeError("gateway unreachable")

    import app.controllers.payment_controller as controller

    monkeypatch.setattr(controller, "_verify_kutanapay_payment", exploding_verify)

    db = _run(
        [
            _FakeTransaction("kutanapay", "odos-boom"),
            _FakeTransaction("kutanapay", "odos-fine"),
        ]
    )
    assert seen == ["odos-boom", "odos-fine"]
    assert db.rollbacks == 1


def test_grace_and_giveup_windows_are_still_bounded():
    """A payment is given time to settle on its own before the loop steps in,
    and is abandoned after a day rather than retried forever."""
    assert svc.GRACE_WINDOW_MINUTES > 0
    assert timedelta(hours=svc.GIVE_UP_AFTER_HOURS) >= timedelta(hours=1)
    assert frozenset({"kutanapay", "paystack"}) == svc.RECONCILABLE_PROVIDERS
