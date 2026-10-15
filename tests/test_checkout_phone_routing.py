"""The MoMo number a customer picks at checkout is the one that gets charged.

KutanaPay infers the mobile money network from the phone number it is handed.
So sending the wrong number does not merely mislabel the payment -- it routes
it to a different network entirely.

That is not hypothetical. A customer whose profile number was Telecel selected
MTN at checkout; the backend sent the profile number, KutanaPay routed to
Telecel, and no prompt ever arrived, because Telecel Cash authorises by USSD
rather than a push notification. The payment simply expired.

`payment_phone` is optional on the payload, so the profile number stays as the
fallback for a card payment or any flow that does not nominate one.
"""

from __future__ import annotations

import pytest


def _resolve(payment_phone, profile_phone):
    """The one line under test, as the controller evaluates it."""
    return payment_phone or profile_phone


class TestSelectedNumberWins:
    def test_chosen_momo_number_is_used(self):
        assert _resolve("0241234567", "0202972072") == "0241234567"

    def test_a_telecel_profile_cannot_override_an_mtn_selection(self):
        """The exact regression: 020 is Telecel, 024 is MTN."""
        chosen_mtn = "0241234567"
        profile_telecel = "0202972072"
        assert _resolve(chosen_mtn, profile_telecel) == chosen_mtn


class TestFallback:
    @pytest.mark.parametrize("empty", [None, ""])
    def test_profile_number_is_used_when_none_was_chosen(self, empty):
        """Card payments nominate no number, so the profile one is correct."""
        assert _resolve(empty, "0202972072") == "0202972072"

    def test_both_absent_is_none_not_an_error(self):
        assert _resolve(None, None) is None


class TestControllerWiring:
    def test_the_controller_passes_the_payload_number(self, monkeypatch):
        """Guards the wiring itself: the fix is one argument, and a future
        edit could quietly swap it back to current_user.phone_number."""
        import inspect

        from app.controllers import payment_controller

        source = inspect.getsource(
            payment_controller.create_kutanapay_checkout_session
        )
        assert "customer_phone=payload.payment_phone or current_user.phone_number" in (
            source
        ), "order checkout must prefer the customer's selected MoMo number"
