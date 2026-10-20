"""A customer's abandoned checkouts are cancelled before a new one opens.

KutanaPay refuses a customer's sixth simultaneously-pending checkout:

    Customer <email> has reached the limit of 5 pending checkouts.

Every attempt in this codebase creates a fresh order and a fresh checkout, so
a customer who backs out and retries a handful of times reaches that limit and
then cannot pay at all. What they see is "the payment provider rejected this
checkout", which points nowhere near the real cause -- it reads as a card or
network problem, and the customer usually gives up.

The risky half of this is the local bookkeeping, not the cancelling: marking a
transaction cancelled when the gateway did not actually cancel it would hide a
real payment from the reconciliation loop, which only looks at pending rows.
So local state follows the gateway's answer, never leads it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.controllers import payment_controller as ctrl


class _Transaction:
    def __init__(self, reference, checkout_id="chk_1", status="pending"):
        self.reference = reference
        self.provider_transaction_id = checkout_id
        self.status = status
        self.gateway_response = None
        self.user_id = "u1"
        self.provider = "kutanapay"
        self.created_at = datetime.now(UTC)


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows
        self.commits = 0

    def scalars(self, _stmt):
        return _Scalars(self._rows)

    def commit(self):
        self.commits += 1


class _User:
    id = "u1"
    email = "a@b.c"


@pytest.fixture
def cancelled(monkeypatch):
    calls = []

    def fake_cancel(checkout_id):
        calls.append(checkout_id)
        return True

    monkeypatch.setattr(ctrl, "kutanapay_cancel_checkout", fake_cancel)
    return calls


class TestCancelsAbandonedCheckouts:
    def test_a_pending_checkout_is_cancelled_at_the_gateway(self, cancelled):
        tx = _Transaction("odos-old", checkout_id="chk_old")
        ctrl._release_stale_checkouts(_Session([tx]), _User())
        assert cancelled == ["chk_old"]
        assert tx.status == "cancelled"

    def test_several_are_all_cancelled(self, cancelled):
        rows = [_Transaction(f"odos-{i}", checkout_id=f"chk_{i}") for i in range(3)]
        ctrl._release_stale_checkouts(_Session(rows), _User())
        assert cancelled == ["chk_0", "chk_1", "chk_2"]
        assert all(t.status == "cancelled" for t in rows)

    def test_nothing_to_do_does_not_commit(self, cancelled):
        db = _Session([])
        ctrl._release_stale_checkouts(db, _User())
        assert cancelled == []
        assert db.commits == 0


class TestLocalStateFollowsTheGateway:
    def test_a_refused_cancellation_leaves_the_row_pending(self, monkeypatch):
        """The dangerous case. A checkout the customer has just paid cannot be
        cancelled, and marking it cancelled here would hide a real payment
        from the reconciliation loop, which only re-checks pending rows."""
        monkeypatch.setattr(ctrl, "kutanapay_cancel_checkout", lambda _id: False)
        tx = _Transaction("odos-just-paid")
        ctrl._release_stale_checkouts(_Session([tx]), _User())
        assert tx.status == "pending", (
            "a transaction the gateway would not cancel must stay pending"
        )
        assert tx.gateway_response is None

    def test_a_raising_gateway_leaves_the_row_pending(self, monkeypatch):
        def explode(_id):
            raise RuntimeError("gateway down")

        monkeypatch.setattr(ctrl, "kutanapay_cancel_checkout", explode)
        tx = _Transaction("odos-unreachable")
        ctrl._release_stale_checkouts(_Session([tx]), _User())
        assert tx.status == "pending"


class TestNeverBlocksTheCustomer:
    def test_a_gateway_failure_does_not_propagate(self, monkeypatch):
        """Failing to tidy up must not stop the customer starting a checkout --
        that would turn a housekeeping problem into a lost sale."""
        monkeypatch.setattr(
            ctrl,
            "kutanapay_cancel_checkout",
            lambda _id: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        ctrl._release_stale_checkouts(_Session([_Transaction("odos-x")]), _User())

    def test_one_failure_does_not_stop_the_others(self, monkeypatch):
        seen = []

        def flaky(checkout_id):
            seen.append(checkout_id)
            if checkout_id == "chk_1":
                raise RuntimeError("boom")
            return True

        monkeypatch.setattr(ctrl, "kutanapay_cancel_checkout", flaky)
        rows = [_Transaction(f"odos-{i}", checkout_id=f"chk_{i}") for i in range(3)]
        ctrl._release_stale_checkouts(_Session(rows), _User())
        assert seen == ["chk_0", "chk_1", "chk_2"]
        assert rows[0].status == "cancelled"
        assert rows[1].status == "pending"
        assert rows[2].status == "cancelled"


class TestBounds:
    def test_the_cancellation_batch_is_capped(self):
        """Each cancellation is an HTTP round trip and this runs inline before
        the customer's checkout opens."""
        assert ctrl._MAX_STALE_CHECKOUTS_TO_CANCEL <= 5

    def test_the_window_covers_the_gateway_expiry(self):
        """Checkouts expire in about five minutes; older ones have already
        stopped counting against the limit."""
        assert ctrl._STALE_CHECKOUT_WINDOW_MINUTES >= 10
