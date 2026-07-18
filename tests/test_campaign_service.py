"""Unit tests for merchandising campaign resolution helpers."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.services.campaign_service import (
    campaign_is_live,
    derive_campaign_status,
    slugify_campaign,
)


def _campaign(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "id": uuid4(),
        "slug": "summer-sale",
        "title": "Summer Sale",
        "is_active": True,
        "visibility": "public",
        "status": "active",
        "starts_at": now - timedelta(hours=1),
        "ends_at": now + timedelta(days=2),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_slugify_campaign_normalizes_title():
    assert slugify_campaign("Summer Sale 2026!") == "summer-sale-2026"
    assert slugify_campaign("  ") == "campaign"


def test_campaign_is_live_respects_schedule_and_visibility():
    now = datetime.now(timezone.utc)
    assert campaign_is_live(_campaign()) is True
    assert campaign_is_live(_campaign(is_active=False)) is False
    assert campaign_is_live(_campaign(visibility="hidden")) is False
    assert campaign_is_live(_campaign(status="draft")) is False
    assert campaign_is_live(_campaign(starts_at=now + timedelta(days=1))) is False
    assert campaign_is_live(_campaign(ends_at=now - timedelta(hours=1))) is False


def test_derive_campaign_status_from_schedule():
    now = datetime.now(timezone.utc)
    assert derive_campaign_status(_campaign(status="archived")) == "archived"
    assert derive_campaign_status(_campaign(status="draft")) == "draft"
    assert (
        derive_campaign_status(
            _campaign(status="active", starts_at=now + timedelta(days=1))
        )
        == "scheduled"
    )
    assert (
        derive_campaign_status(
            _campaign(status="active", ends_at=now - timedelta(hours=1))
        )
        == "ended"
    )
    assert derive_campaign_status(_campaign(status="active")) == "active"
