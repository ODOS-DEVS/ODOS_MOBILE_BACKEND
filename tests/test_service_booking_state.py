"""The booking lifecycle refuses illegal moves and the wrong mover.

Most of these are abuse cases rather than happy paths: the state machine
exists so that neither party can take an action the marketplace has not
agreed to — a customer marking work complete to force a payout, a provider
confirming a booking nobody paid for, anyone reviving a cancelled job.
"""

from __future__ import annotations

import pytest

from app.services.service_booking_state import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    BookingTransitionError,
    assert_transition,
    can_transition,
    is_terminal,
    releases_slot,
)


class TestLegalFlows:
    def test_request_flow_end_to_end(self):
        """Request -> accept -> pay -> work -> done."""
        assert can_transition("pending_provider", "pending_payment", actor_role="provider").allowed
        assert can_transition("pending_payment", "confirmed", actor_role="system").allowed
        assert can_transition("confirmed", "in_progress", actor_role="provider").allowed
        assert can_transition("in_progress", "completed", actor_role="provider").allowed

    def test_pay_on_completion_confirms_without_payment(self):
        """Acceptance alone holds the slot when money comes later."""
        assert can_transition("pending_provider", "confirmed", actor_role="provider").allowed

    def test_provider_may_decline_a_request(self):
        assert can_transition("pending_provider", "declined", actor_role="provider").allowed

    def test_customer_may_cancel_before_the_provider_answers(self):
        assert can_transition("pending_provider", "cancelled", actor_role="customer").allowed


class TestIllegalTransitions:
    @pytest.mark.parametrize(
        "current,target",
        [
            ("cancelled", "confirmed"),
            ("declined", "confirmed"),
            ("expired", "confirmed"),
            ("completed", "in_progress"),
            ("pending_provider", "completed"),
            ("pending_provider", "in_progress"),
            ("pending_payment", "in_progress"),
        ],
    )
    def test_refused_for_everyone_including_admin(self, current, target):
        for role in ("customer", "provider", "admin", "system"):
            assert not can_transition(current, target, actor_role=role).allowed

    def test_a_booking_cannot_transition_to_itself(self):
        assert not can_transition("confirmed", "confirmed", actor_role="provider").allowed

    def test_unknown_status_is_rejected(self):
        assert not can_transition("banana", "confirmed", actor_role="admin").allowed


class TestActorPermissions:
    def test_customer_cannot_complete_their_own_booking(self):
        """Otherwise a customer could trigger the provider payout at will."""
        check = can_transition("confirmed", "completed", actor_role="customer")
        assert not check.allowed
        assert "permission" in (check.reason or "")

    def test_customer_cannot_start_the_work(self):
        assert not can_transition("confirmed", "in_progress", actor_role="customer").allowed

    def test_provider_cannot_confirm_an_unpaid_booking(self):
        """Confirmation out of pending_payment belongs to payment verification."""
        check = can_transition("pending_payment", "confirmed", actor_role="provider")
        assert not check.allowed
        assert "Only Odos" in (check.reason or "")

    def test_customer_cannot_confirm_their_own_payment(self):
        assert not can_transition("pending_payment", "confirmed", actor_role="customer").allowed

    def test_provider_cannot_accept_on_the_customers_behalf_then_dispute(self):
        assert not can_transition("completed", "disputed", actor_role="provider").allowed

    def test_only_the_customer_opens_a_dispute(self):
        assert can_transition("completed", "disputed", actor_role="customer").allowed

    def test_both_parties_may_cancel_a_confirmed_booking(self):
        assert can_transition("confirmed", "cancelled", actor_role="customer").allowed
        assert can_transition("confirmed", "cancelled", actor_role="provider").allowed


class TestAdminAndSystem:
    def test_admin_may_resolve_a_dispute_either_way(self):
        assert can_transition("disputed", "completed", actor_role="admin").allowed
        assert can_transition("disputed", "cancelled", actor_role="admin").allowed

    def test_neither_party_resolves_their_own_dispute(self):
        for role in ("customer", "provider"):
            assert not can_transition("disputed", "completed", actor_role=role).allowed
            assert not can_transition("disputed", "cancelled", actor_role=role).allowed

    def test_only_the_system_expires_an_unpaid_booking(self):
        assert can_transition("pending_payment", "expired", actor_role="system").allowed
        for role in ("customer", "provider"):
            assert not can_transition("pending_payment", "expired", actor_role=role).allowed

    def test_admin_cannot_invent_an_illegal_transition(self):
        """Admin bypasses the actor check, never the legality check."""
        assert not can_transition("cancelled", "completed", actor_role="admin").allowed


class TestTerminalStates:
    @pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES))
    def test_terminal_states_have_no_exits_except_dispute(self, status):
        exits = TRANSITIONS[status]
        assert exits <= {"disputed"}
        assert is_terminal(status)

    def test_cancelled_declined_expired_free_the_slot(self):
        for status in ("cancelled", "declined", "expired"):
            assert releases_slot(status)

    def test_active_states_keep_the_slot(self):
        for status in ("pending_payment", "confirmed", "in_progress"):
            assert not releases_slot(status)


class TestAssertHelper:
    def test_raises_with_a_usable_message(self):
        with pytest.raises(BookingTransitionError, match="permission"):
            assert_transition("confirmed", "completed", actor_role="customer")

    def test_silent_when_allowed(self):
        assert_transition("confirmed", "completed", actor_role="provider")


class TestMachineIntegrity:
    def test_every_target_is_a_declared_state(self):
        for current, targets in TRANSITIONS.items():
            for target in targets:
                assert target in TRANSITIONS, f"{current} -> {target} is not a declared state"

    def test_every_non_terminal_state_can_be_left(self):
        for status, targets in TRANSITIONS.items():
            if status not in TERMINAL_STATUSES:
                assert targets, f"{status} is a dead end"
