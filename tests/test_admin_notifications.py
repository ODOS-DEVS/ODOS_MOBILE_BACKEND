"""Regression tests for admin approval-request notifications: the SMS
fan-out helper added alongside the existing admin alert emails, and the
three new email renderers for return requests, flash sale nominations,
and campaign opt-ins."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.email_service import (
    send_admin_campaign_opt_in_email,
    send_admin_flash_sale_nomination_email,
    send_admin_return_request_email,
)
from app.services.sms_service import notify_admins_by_sms


def _admin(phone_number: str | None):
    return SimpleNamespace(id="admin-1", phone_number=phone_number, email="admin@odos.app")


# --- notify_admins_by_sms ---


def test_notify_admins_by_sms_texts_every_admin_with_a_phone(monkeypatch):
    admins = [_admin("+233111111111"), _admin("+233222222222")]
    sent = []

    monkeypatch.setattr(
        "app.services.sms_service.list_admins_with_feature",
        lambda db, feature: admins,
    )
    monkeypatch.setattr(
        "app.services.sms_service.send_sms",
        lambda *, phone_number, message: sent.append((phone_number, message)),
    )

    notify_admins_by_sms(db=None, feature="returns", message="hello")

    assert len(sent) == 2
    assert sent[0][1] == "hello"


def test_notify_admins_by_sms_skips_admins_without_a_phone_number(monkeypatch):
    """Reproduces the original gap: admins with no phone on file must be
    skipped silently rather than crashing the request that triggered the
    notification."""
    admins = [_admin(None), _admin("+233111111111")]
    sent = []

    monkeypatch.setattr(
        "app.services.sms_service.list_admins_with_feature",
        lambda db, feature: admins,
    )
    monkeypatch.setattr(
        "app.services.sms_service.send_sms",
        lambda *, phone_number, message: sent.append(phone_number),
    )

    notify_admins_by_sms(db=None, feature="returns", message="hello")

    assert sent == ["+233111111111"]


def test_notify_admins_by_sms_does_not_raise_when_arkesel_fails(monkeypatch):
    """A downed SMS provider must not break the approval-request flow that
    triggered the alert — mirrors the try/except-per-admin pattern already
    used for the admin alert emails."""
    from app.services.arkesel_service import ArkeselSmsError

    monkeypatch.setattr(
        "app.services.sms_service.list_admins_with_feature",
        lambda db, feature: [_admin("+233111111111")],
    )

    def _raise(*, phone_number, message):
        raise ArkeselSmsError("boom")

    monkeypatch.setattr("app.services.sms_service.send_sms", _raise)

    # Should not raise.
    notify_admins_by_sms(db=None, feature="returns", message="hello")


# --- New admin alert emails: same double-escape guard as the other four ---


def test_return_request_email_heading_is_not_double_escaped():
    html, text = _render(
        send_admin_return_request_email,
        customer_name="Ama & Co",
        order_number="1234",
        item_title="Sneakers",
        request_type="refund",
        quantity=1,
        submitted_at_label="1 Jan 2026",
        return_request_id="req-1",
    )
    assert "&lt;strong&gt;" not in html
    assert "<strong>Ama &amp; Co</strong>" in html
    assert "<strong>" not in text


def test_flash_sale_nomination_email_heading_is_not_double_escaped():
    html, text = _render(
        send_admin_flash_sale_nomination_email,
        store_name="Vera Aura",
        product_title="Summer Dress",
        submitted_at_label="1 Jan 2026",
    )
    assert "&lt;strong&gt;" not in html
    assert "<strong>Vera Aura</strong>" in html
    assert "<strong>" not in text


def test_campaign_opt_in_email_heading_is_not_double_escaped():
    html, text = _render(
        send_admin_campaign_opt_in_email,
        vendor_name="Nationwide Sneakers",
        campaign_title="Back to School",
        product_title="Air Max",
        submitted_at_label="1 Jan 2026",
    )
    assert "&lt;strong&gt;" not in html
    assert "<strong>Nationwide Sneakers</strong>" in html
    assert "<strong>" not in text


def _render(send_fn, **kwargs):
    """Capture the (html, text) content a send_admin_*_email function would
    mail out, without hitting the network."""
    captured = {}

    def _fake_send(**call_kwargs):
        captured.update(call_kwargs)

    import app.services.email_service as email_service

    original = email_service.send_transactional_email
    email_service.send_transactional_email = _fake_send
    try:
        send_fn(to_email="admin@odos.app", to_name="Admin", admin_panel_url="", **kwargs)
    finally:
        email_service.send_transactional_email = original

    return captured["html_content"], captured["text_content"]
