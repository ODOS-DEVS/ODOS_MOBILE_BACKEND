"""Unit tests for the return-request state machine (the shared source of
truth both the admin and vendor endpoints now delegate to).

The concurrency fix itself (row-locking the return request, and the
database-level partial-unique-index backstop that stops two different
return requests on the same order from colliding — the actual bug this
audit found: an order with two returned items would raise IntegrityError
on the second refund) needs a live Postgres session and was verified
directly against one, mirroring this test suite's existing convention for
stateful/DB-level behavior (see test_delivery_lifecycle.py's docstring).
What's unit-tested here is the deterministic part: the transition table
itself, and that illegal transitions are rejected with the right message
shape."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import settings
from app.services.return_request_service import (
    ALLOWED_RETURN_STATUS_TRANSITIONS,
    STATUSES_REQUIRING_GOODS_ACCOUNTED,
    SUPPORTED_RETURN_REQUEST_STATUSES,
    TERMINAL_RETURN_STATUSES,
    VENDOR_RETURN_REQUEST_STATUSES,
    ReturnRequestIllegalTransitionError,
    exceeds_vendor_self_refund_limit,
    goods_are_accounted_for,
)


def test_terminal_statuses_have_no_outgoing_transitions():
    """Reproduces the original bug: the admin endpoint had no state-machine
    validation at all, so `refunded -> refunded` (or `rejected -> refunded`)
    was silently accepted — which is exactly the shape of a double-refund."""
    for terminal_status in TERMINAL_RETURN_STATUSES:
        assert ALLOWED_RETURN_STATUS_TRANSITIONS[terminal_status] == set()


def test_terminal_statuses_are_exactly_rejected_refunded_exchanged():
    assert TERMINAL_RETURN_STATUSES == {"rejected", "refunded", "exchanged"}


def test_open_statuses_may_stay_put():
    """A status-only-metadata edit (e.g. an admin updating just the note)
    must not be treated as an illegal transition when nothing about the
    resolution has actually changed."""
    for open_status in ("requested", "under_review", "approved"):
        assert open_status in ALLOWED_RETURN_STATUS_TRANSITIONS[open_status]


def test_every_open_status_can_reach_every_terminal_status():
    """Matches the pre-existing vendor-only guard this replaces: requested,
    under_review, and approved can all move straight to refunded (and the
    other terminal outcomes) without forcing every request through every
    intermediate state.

    Note this is only the *transition* table. Reaching "refunded" is also
    subject to goods_are_accounted_for(), which is what actually stops a
    payout for an item nobody has confirmed came back — see the custody
    tests below."""
    for open_status in ("requested", "under_review", "approved"):
        for terminal_status in TERMINAL_RETURN_STATUSES:
            assert terminal_status in ALLOWED_RETURN_STATUS_TRANSITIONS[open_status]


def test_all_transition_table_keys_and_values_are_supported_statuses():
    for current_status, next_statuses in ALLOWED_RETURN_STATUS_TRANSITIONS.items():
        assert current_status in SUPPORTED_RETURN_REQUEST_STATUSES
        assert next_statuses <= SUPPORTED_RETURN_REQUEST_STATUSES


def test_vendor_statuses_are_a_subset_of_supported_statuses():
    """Vendors can't set a return back to "requested" (that's a customer/
    admin-only reopen) — confirms that restriction is still in place."""
    assert VENDOR_RETURN_REQUEST_STATUSES <= SUPPORTED_RETURN_REQUEST_STATUSES
    assert "requested" not in VENDOR_RETURN_REQUEST_STATUSES


def test_illegal_transition_error_message_for_terminal_status():
    error = ReturnRequestIllegalTransitionError("refunded", "approved")
    message = str(error)
    assert "already refunded" in message
    assert "can't be changed again" in message


def test_illegal_transition_error_message_for_open_status():
    error = ReturnRequestIllegalTransitionError("requested", "exchanged")
    # This particular pair is actually allowed by the table (requested can
    # reach exchanged) — this test only checks the *message shape* the
    # error produces when constructed directly, not that this pair is
    # illegal.
    message = str(error)
    assert "requested" in message
    assert "exchanged" in message


# --- custody: money must not move for goods nobody has seen ---


def test_refund_blocked_when_nothing_confirms_the_item_came_back():
    """The expensive failure mode: approve, refund, and the customer keeps both
    the item and the money. Neither a receipt nor a waiver means nobody has
    accounted for the goods."""
    assert goods_are_accounted_for(received_at=None, return_waived=False) is False


def test_refund_allowed_once_the_item_is_received():
    assert (
        goods_are_accounted_for(received_at=datetime(2026, 8, 31, tzinfo=UTC), return_waived=False)
        is True
    )


def test_refund_allowed_when_the_return_is_deliberately_waived():
    """A seller who does not want a damaged item back can still be refunded —
    but it has to be chosen, not reached by leaving a field unset."""
    assert goods_are_accounted_for(received_at=None, return_waived=True) is True


def test_only_refund_and_exchange_are_gated_on_custody():
    """Rejecting a request, or moving it through review, moves no money and must
    not require the goods."""
    assert STATUSES_REQUIRING_GOODS_ACCOUNTED == {"refunded", "exchanged"}
    for status in ("requested", "under_review", "approved", "awaiting_return", "rejected"):
        assert status not in STATUSES_REQUIRING_GOODS_ACCOUNTED


# --- custody statuses are part of the state machine ---


def test_custody_statuses_are_supported():
    assert {"awaiting_return", "received"} <= SUPPORTED_RETURN_REQUEST_STATUSES


def test_awaiting_return_can_only_progress_to_received_or_a_terminal_outcome():
    assert ALLOWED_RETURN_STATUS_TRANSITIONS["awaiting_return"] == {
        "awaiting_return",
        "received",
        "refunded",
        "exchanged",
        "rejected",
    }


def test_received_cannot_go_back_to_an_earlier_stage():
    """Once an item is logged as received, the record of that must not be
    walked backwards to re-open the custody question."""
    for earlier in ("requested", "under_review", "approved", "awaiting_return"):
        assert earlier not in ALLOWED_RETURN_STATUS_TRANSITIONS["received"]


def test_vendors_may_drive_custody_but_not_reopen_a_request():
    assert {"awaiting_return", "received"} <= VENDOR_RETURN_REQUEST_STATUSES
    assert "requested" not in VENDOR_RETURN_REQUEST_STATUSES


# --- vendor self-refund limit ---


def test_vendor_may_settle_a_small_refund_alone():
    assert exceeds_vendor_self_refund_limit(settings.vendor_self_refund_limit - 1) is False


def test_large_refund_needs_odos_approval():
    assert exceeds_vendor_self_refund_limit(settings.vendor_self_refund_limit + 1) is True


def test_refund_exactly_at_the_limit_is_still_self_service():
    """A boundary that pushes the common case into an admin queue for no reason
    is a support cost, so the limit is inclusive."""
    assert exceeds_vendor_self_refund_limit(settings.vendor_self_refund_limit) is False


def test_unknown_refund_amount_is_not_treated_as_over_the_limit():
    """refund_amount is nullable until someone sets it; None must not be read as
    an enormous refund."""
    assert exceeds_vendor_self_refund_limit(None) is False
