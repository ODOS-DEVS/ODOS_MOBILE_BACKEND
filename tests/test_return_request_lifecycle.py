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

from app.services.return_request_service import (
    ALLOWED_RETURN_STATUS_TRANSITIONS,
    SUPPORTED_RETURN_REQUEST_STATUSES,
    TERMINAL_RETURN_STATUSES,
    VENDOR_RETURN_REQUEST_STATUSES,
    ReturnRequestIllegalTransitionError,
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
    intermediate state."""
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
