"""Wallet top-ups must not run against a test payment gateway.

The reason is not tidiness. A top-up credits `available_balance` directly, and
that balance buys real goods from real vendors through `create_wallet_checkout`.
Paystack publishes its test card numbers in its own documentation. So with an
`sk_test_` key in a live app the full chain is open to anyone: top up with a
public test card, Paystack test reports success, `_reconcile_wallet_topup`
credits the wallet, and the wallet pays for an order a vendor then ships.

The gate fails closed -- a key that cannot be positively identified as live is
treated as not live -- and can only be overridden by a setting that has to be
turned on deliberately.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.core.config import settings_secret_is_live


class TestLiveKeyDetection:
    @pytest.mark.parametrize(
        "key",
        [
            "sk_live_abc123",
            "  sk_live_padded  ",
        ],
    )
    def test_live_keys_are_recognised(self, key):
        assert settings_secret_is_live(key) is True

    @pytest.mark.parametrize(
        "key",
        [
            "sk_test_abc123",
            "pk_live_abc123",
            "SK_LIVE_ABC123",
            "sk_liv",
            "live_sk_abc",
            "replace-with-paystack-secret",
            "",
            "   ",
            None,
        ],
    )
    def test_anything_not_positively_live_reads_as_not_live(self, key):
        """Unset, malformed, wrong-case and unrecognised all fail closed. A key
        we cannot classify must not be assumed to move real money."""
        assert settings_secret_is_live(key) is False


class TestTopUpGate:
    @pytest.fixture
    def gate(self, monkeypatch):
        from app.core.config import settings

        def configure(*, secret_key: str, allow_test: bool):
            monkeypatch.setattr(settings, "paystack_secret_key", secret_key)
            monkeypatch.setattr(
                settings, "wallet_topups_allow_test_gateway", allow_test
            )
            # Hold KutanaPay off so these cases isolate the Paystack arm of
            # the gate -- otherwise a live KutanaPay in the environment would
            # satisfy it and the assertions would pass for the wrong reason.
            monkeypatch.setattr(settings, "kutanapay_live", False)
            return settings

        return configure

    def test_test_key_blocks_topups(self, gate):
        s = gate(secret_key="sk_test_abc", allow_test=False)
        assert s.paystack_is_live is False
        assert s.wallet_topups_are_allowed is False

    def test_live_key_allows_topups(self, gate):
        s = gate(secret_key="sk_live_abc", allow_test=False)
        assert s.paystack_is_live is True
        assert s.wallet_topups_are_allowed is True

    def test_missing_key_blocks_topups(self, gate):
        s = gate(secret_key="", allow_test=False)
        assert s.wallet_topups_are_allowed is False

    def test_staging_override_allows_a_test_gateway(self, gate):
        """Staging is the one place crediting a wallet without real money is
        the point. It takes an explicit setting, not just a test key."""
        s = gate(secret_key="sk_test_abc", allow_test=True)
        assert s.paystack_is_live is False
        assert s.wallet_topups_are_allowed is True


class TestTopUpEndpointRefuses:
    def test_initialize_refuses_before_touching_the_gateway(self, monkeypatch):
        """The refusal has to come before any row is written or any gateway
        call is made -- a half-created top-up is worse than a clean 503."""
        from app.controllers import customer_wallet_controller as ctrl

        monkeypatch.setattr(ctrl.settings, "paystack_secret_key", "sk_test_abc")
        monkeypatch.setattr(
            ctrl.settings, "wallet_topups_allow_test_gateway", False
        )

        def must_not_run(*_a, **_k):
            raise AssertionError("gateway was called despite the gate")

        monkeypatch.setattr(ctrl, "initialize_transaction", must_not_run)
        monkeypatch.setattr(
            ctrl,
            "get_or_create_customer_wallet",
            lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError("wallet was touched despite the gate")
            ),
        )

        class _Payload:
            amount = 50.0
            payment_type = "card"
            payment_label = None
            payment_network = None
            payment_phone = None
            payment_last4 = None
            callback_url = None
            cancel_url = None

        with pytest.raises(HTTPException) as exc:
            ctrl.initialize_wallet_topup(
                db=object(),
                request=object(),
                current_user=object(),
                payload=_Payload(),
            )

        assert exc.value.status_code == 503
        assert "temporarily unavailable" in exc.value.detail.lower()

    def test_live_key_lets_the_gate_through(self, monkeypatch):
        """Proves the 503 above is the gate and not an unrelated failure: with
        a live key the same call gets past it and reaches the wallet lookup."""
        from app.controllers import customer_wallet_controller as ctrl

        monkeypatch.setattr(ctrl.settings, "paystack_secret_key", "sk_live_abc")
        monkeypatch.setattr(
            ctrl.settings, "wallet_topups_allow_test_gateway", False
        )

        reached = []

        def record_wallet(*_a, **_k):
            reached.append("wallet")
            raise RuntimeError("stop here")

        monkeypatch.setattr(ctrl, "get_or_create_customer_wallet", record_wallet)

        class _Payload:
            amount = 50.0
            payment_type = "card"
            payment_label = None
            payment_network = None
            payment_phone = None
            payment_last4 = None
            callback_url = None
            cancel_url = None

        class _User:
            id = "11111111-1111-1111-1111-111111111111"

        with pytest.raises(RuntimeError, match="stop here"):
            ctrl.initialize_wallet_topup(
                db=object(),
                request=object(),
                current_user=_User(),
                payload=_Payload(),
            )

        assert reached == ["wallet"]


class TestKutanaPayLiveDetection:
    """Whether KutanaPay is live has to be declared, not inferred.

    A KutanaPay key carries no mode the way Paystack's `sk_live_` prefix does,
    and the host is not a signal either -- live and sandbox are both served
    from api.kutanapay.com and differ only by which key is presented. Guessing
    from the host blocks a working gateway or, worse, credits a real balance
    from a sandbox one, so the flag has to be set deliberately.
    """

    @pytest.fixture
    def cfg(self, monkeypatch):
        from app.core.config import settings

        def configure(*, api_key: str = "kp_abc", live: bool = False):
            monkeypatch.setattr(settings, "kutanapay_api_key", api_key)
            monkeypatch.setattr(settings, "kutanapay_live", live)
            return settings

        return configure

    def test_configured_but_not_declared_live_is_not_live(self, cfg):
        assert cfg(live=False).kutanapay_is_live is False

    def test_declared_live_is_live(self, cfg):
        assert cfg(live=True).kutanapay_is_live is True

    def test_the_flag_alone_is_not_enough_without_a_key(self, cfg):
        """A declaration cannot conjure credentials."""
        assert cfg(api_key="", live=True).kutanapay_is_live is False

    def test_blank_key_is_not_configured(self, cfg):
        assert cfg(api_key="   ", live=True).kutanapay_is_live is False



class TestTopUpProviderChoice:
    @pytest.fixture
    def cfg(self, monkeypatch):
        from app.core.config import settings

        def configure(*, paystack: str, kutana_live: bool, allow_test: bool = False):
            monkeypatch.setattr(settings, "paystack_secret_key", paystack)
            monkeypatch.setattr(settings, "kutanapay_api_key", "kp_abc")
            monkeypatch.setattr(settings, "kutanapay_live", kutana_live)
            monkeypatch.setattr(settings, "wallet_topups_allow_test_gateway", allow_test)
            return settings

        return configure

    def test_kutanapay_is_preferred_when_live(self, cfg):
        """It is the collections provider, so it takes the top-up even though
        Paystack is also live and could have."""
        s = cfg(paystack="sk_live_abc", kutana_live=True)
        assert s.wallet_topup_provider == "kutanapay"

    def test_live_kutanapay_unblocks_topups_on_a_paystack_test_key(self, cfg):
        """The Paystack key is irrelevant once KutanaPay is taking the money --
        this is the case that had top-ups returning 503 in production."""
        s = cfg(paystack="sk_test_abc", kutana_live=True)
        assert s.paystack_is_live is False
        assert s.wallet_topup_provider == "kutanapay"
        assert s.wallet_topups_are_allowed is True

    def test_falls_back_to_paystack_when_kutanapay_is_not_live(self, cfg):
        s = cfg(paystack="sk_live_abc", kutana_live=False)
        assert s.wallet_topup_provider == "paystack"

    def test_neither_live_blocks_topups(self, cfg):
        s = cfg(paystack="sk_test_abc", kutana_live=False)
        assert s.wallet_topup_provider is None
        assert s.wallet_topups_are_allowed is False


class TestTopUpRouting:
    def test_a_live_kutanapay_topup_never_calls_paystack(self, monkeypatch):
        """Routing is on the gateway that is live, not on what the client asked
        for -- opening a Paystack session here would charge through a test key."""
        from app.controllers import customer_wallet_controller as ctrl

        monkeypatch.setattr(ctrl.settings, "paystack_secret_key", "sk_test_abc")
        monkeypatch.setattr(ctrl.settings, "kutanapay_api_key", "kp_abc")
        monkeypatch.setattr(ctrl.settings, "kutanapay_live", True)
        monkeypatch.setattr(ctrl.settings, "wallet_topups_allow_test_gateway", False)

        def must_not_run(*_a, **_k):
            raise AssertionError("Paystack was called for a KutanaPay top-up")

        monkeypatch.setattr(ctrl, "initialize_transaction", must_not_run)

        reached = []

        def record(*_a, **_k):
            reached.append("kutanapay")
            raise RuntimeError("stop here")

        monkeypatch.setattr(ctrl, "_initialize_kutanapay_topup", record)
        monkeypatch.setattr(
            ctrl, "get_or_create_customer_wallet", lambda *_a, **_k: object()
        )

        class _Payload:
            amount = 50.0
            payment_type = "momo"
            payment_label = None
            payment_network = None
            payment_phone = "0541234567"
            payment_last4 = None
            callback_url = None
            cancel_url = None

        class _User:
            id = "11111111-1111-1111-1111-111111111111"

        with pytest.raises(RuntimeError, match="stop here"):
            ctrl.initialize_wallet_topup(
                db=object(), request=object(), current_user=_User(), payload=_Payload()
            )

        assert reached == ["kutanapay"]
