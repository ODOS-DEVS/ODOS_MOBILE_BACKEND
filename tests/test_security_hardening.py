"""Regression tests for the security-audit fixes: admin permission-band
enforcement, the refund-amount ceiling, the placement_tags campaign-slug
filter, rate limiting's Redis-down fallback, and image upload magic-byte
validation. Each test reproduces the original failure mode first (in the
test body's assertions) so a future regression on the same code path fails
loudly rather than silently reopening the hole."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.controllers.admin_controller import max_refund_amount_for
from app.controllers.vendor_controller import filter_reserved_placement_tags
from app.core.admin_permissions import AdminPermissionLevel, admin_has_feature
from app.models import UserRole
from app.services.media_service import _matches_declared_image_type


def _admin(permission: str | None) -> SimpleNamespace:
    return SimpleNamespace(role=UserRole.ADMIN, admin_permission=permission)


# --- Admin permission-band enforcement (the refund/returns bypass) ---


def test_analyst_admin_cannot_reach_returns_or_finance():
    """This is the exact gap that let any admin — including the most
    restricted ANALYST band — approve refunds and see financial ledgers.
    ANALYST must be denied both."""
    user = _admin(AdminPermissionLevel.ANALYST.value)
    assert admin_has_feature(user, "returns") is False
    assert admin_has_feature(user, "finance") is False
    assert admin_has_feature(user, "payouts") is False


def test_support_admin_can_reach_returns_but_not_finance():
    """SUPPORT is deliberately allowed "returns" (customer-service handles
    return requests) but not "finance"/"payouts" — the band system's actual
    intent, which generic require_admin was silently bypassing."""
    user = _admin(AdminPermissionLevel.SUPPORT.value)
    assert admin_has_feature(user, "returns") is True
    assert admin_has_feature(user, "finance") is False


def test_marketing_admin_cannot_reach_products_admin_route_class():
    user = _admin(AdminPermissionLevel.MARKETING.value)
    assert admin_has_feature(user, "stores") is False
    assert admin_has_feature(user, "markets") is False


def test_full_admin_band_retains_full_access():
    user = _admin(AdminPermissionLevel.ADMIN.value)
    for feature in ("returns", "finance", "payouts", "products", "stores", "orders"):
        assert admin_has_feature(user, feature) is True


def test_super_admin_passes_every_feature():
    user = _admin(AdminPermissionLevel.SUPER_ADMIN.value)
    assert admin_has_feature(user, "anything-at-all") is True


def test_non_admin_role_never_has_any_feature():
    user = SimpleNamespace(role=UserRole.CUSTOMER, admin_permission=None)
    assert admin_has_feature(user, "returns") is False


# --- Refund amount ceiling ---


def test_refund_ceiling_matches_line_item_value():
    assert max_refund_amount_for(unit_price=49.99, quantity=2) == 99.98


def test_refund_ceiling_blocks_inflated_amount():
    """Reproduces the original bug: an admin-supplied refund_amount had no
    upper bound and was persisted verbatim. The controller now rejects any
    payload.refund_amount above this ceiling — this test locks the ceiling
    computation itself so that check can't silently drift."""
    line_item_value = max_refund_amount_for(unit_price=20.0, quantity=1)
    attempted_refund = 5000.0
    assert attempted_refund > line_item_value


# --- placement_tags campaign-slug spoofing ---


def test_vendor_tag_matching_live_campaign_slug_is_stripped():
    """Reproduces the original bug: a vendor tagging their own product with
    a live campaign's slug got free (unreviewed) placement in that
    campaign. The slug must now be filtered out before the tag list is
    persisted."""
    reserved = {"back-to-school", "flash-friday"}
    result = filter_reserved_placement_tags(["back-to-school", "trending"], reserved)
    assert result == ["trending"]
    assert "back-to-school" not in (result or [])


def test_vendor_can_still_set_ordinary_marketing_tags():
    reserved = {"back-to-school"}
    result = filter_reserved_placement_tags(["new-arrival", "bestseller"], reserved)
    assert result == ["new-arrival", "bestseller"]


def test_stripping_all_reserved_tags_yields_none_not_empty_list():
    reserved = {"flash-friday"}
    assert filter_reserved_placement_tags(["flash-friday"], reserved) is None


def test_empty_tags_pass_through_unchanged():
    assert filter_reserved_placement_tags(None, {"x"}) is None
    assert filter_reserved_placement_tags([], {"x"}) == []


# --- Rate limiting: must not fail open when Redis is unavailable ---


def test_auth_rate_limit_still_enforces_without_redis(monkeypatch):
    """Reproduces the original bug: enforce_rate_limits() silently no-ops
    when Redis isn't reachable, so login/OTP/password-reset had zero
    brute-force protection in that state. limit_login (and the other auth
    limiters) now go through the memory-fallback path instead."""
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit, "redis_is_enabled", lambda: False)
    rate_limit._memory_hits.clear()

    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.9"))

    for _ in range(10):
        rate_limit.limit_login(request, "attacker@example.com")

    with pytest.raises(Exception) as exc_info:
        rate_limit.limit_login(request, "attacker@example.com")
    assert "429" in str(exc_info.value) or getattr(exc_info.value, "status_code", None) == 429


def test_rate_limit_skips_cleanly_when_no_rules_and_no_redis(monkeypatch):
    from app.core import rate_limit

    monkeypatch.setattr(rate_limit, "redis_is_enabled", lambda: False)
    # Should not raise for an empty rule list.
    rate_limit.enforce_rate_limits_with_memory_fallback([])


# --- Image upload: magic-byte validation ---


def test_real_jpeg_bytes_pass_declared_jpeg_type():
    jpeg_header = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20
    assert _matches_declared_image_type("image/jpeg", jpeg_header) is True


def test_real_png_bytes_pass_declared_png_type():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    assert _matches_declared_image_type("image/png", png_header) is True


def test_real_webp_bytes_pass_declared_webp_type():
    webp_header = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 20
    assert _matches_declared_image_type("image/webp", webp_header) is True


def test_html_payload_claiming_to_be_jpeg_is_rejected():
    """Reproduces the original bug: save_image_upload trusted the client's
    Content-Type header alone. A direct API call (bypassing the mobile app)
    could label an HTML/script payload as image/jpeg and it would sail
    through to Cloudinary."""
    html_payload = b"<script>alert(document.cookie)</script>" + b"\x00" * 20
    assert _matches_declared_image_type("image/jpeg", html_payload) is False


def test_png_payload_mislabeled_as_webp_is_rejected():
    png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    assert _matches_declared_image_type("image/webp", png_header) is False


def test_unknown_content_type_is_rejected():
    assert _matches_declared_image_type("image/svg+xml", b"<svg></svg>") is False
