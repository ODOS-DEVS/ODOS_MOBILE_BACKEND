"""The redirect_url KutanaPay sends customers back to.

The field name was established by probing the live API, not from docs —
KutanaPay publish none. `redirect_url` was the only candidate the API
validated (it rejects a non-URL with "Use an https address or an app link");
`return_url`, `callback_url`, `success_url`, `return_address` and
`success_redirect_url` were all silently ignored and created a checkout
anyway. A rename on their side would therefore fail silently, with customers
quietly stranded on the receipt page again — hence this test.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services import kutanapay_service


@pytest.fixture
def sent_body(monkeypatch):
    """Capture the JSON body create_checkout posts, without posting it."""
    captured = {}

    class _Response:
        status_code = 200
        ok = True

        def json(self):
            return {"data": {"id": "chk_1", "checkout_url": "https://pay.example/c/1"}}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(json or {})
        return _Response()

    monkeypatch.setattr(kutanapay_service.settings, "kutanapay_api_key", "mk_test")
    monkeypatch.setattr(kutanapay_service.requests, "post", fake_post)
    return captured


def create(**kwargs):
    return kutanapay_service.create_checkout(
        amount=10.0,
        reference="odos-kp-1",
        customer_email="shopper@example.com",
        **kwargs,
    )


def test_the_field_is_named_redirect_url(sent_body):
    """If this breaks, customers stop being returned to ODOS after paying."""
    create(redirect_url="https://odos.market/payment/return?reference=odos-kp-1")
    assert sent_body["redirect_url"] == "https://odos.market/payment/return?reference=odos-kp-1"


def test_an_app_link_is_passed_through(sent_body):
    """KutanaPay accepts app links, which is how the mobile app comes back."""
    create(redirect_url="odosmobileexpo://payments/return?reference=odos-kp-1")
    assert sent_body["redirect_url"] == "odosmobileexpo://payments/return?reference=odos-kp-1"


def test_omitting_it_falls_back_to_the_dashboard_setting(sent_body):
    """No key at all, rather than an empty one: the merchant-wide Success
    redirect URL is the fallback, and a blank value would be a 400."""
    create()
    assert "redirect_url" not in sent_body


def test_the_reference_survives_into_the_redirect(sent_body):
    """The point of the per-checkout address: the landing page should not
    have to guess which payment it is confirming."""
    create(redirect_url="https://odos.market/payment/return?reference=odos-kp-xyz")
    assert "reference=odos-kp-xyz" in sent_body["redirect_url"]
