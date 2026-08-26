"""Unit tests for the shared promo analytics aggregation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.routes.vendor_analytics import VENDOR_ENTITY_TYPES, _validate_entity_type
from app.services.promo_analytics_service import (
    ENTITY_TYPES,
    Funnel,
    daily_series,
)


def test_funnel_rates_are_percentages_of_the_previous_step():
    funnel = Funnel(impressions=1000, clicks=250, conversions=50)
    assert funnel.click_through_rate == 25.0
    # Conversion is measured against clicks, not impressions.
    assert funnel.conversion_rate == 20.0


def test_funnel_rates_are_zero_rather_than_dividing_by_zero():
    empty = Funnel()
    assert empty.click_through_rate == 0.0
    assert empty.conversion_rate == 0.0

    # Impressions with no clicks must not blow up the conversion rate.
    seen_only = Funnel(impressions=10)
    assert seen_only.click_through_rate == 0.0
    assert seen_only.conversion_rate == 0.0


def test_daily_series_returns_a_point_for_every_day_including_quiet_ones():
    # An empty entity list short-circuits the query, so no database is needed:
    # this exercises the gap-filling on its own.
    series = daily_series(db=None, entity_type="campaign", entity_ids=[], days=7)

    # `days` is a look-back, so today plus the 7 preceding days.
    assert len(series) == 8
    assert all(point.impressions == 0 for point in series)
    assert all(point.clicks == 0 for point in series)
    assert all(point.conversions == 0 for point in series)


def test_daily_series_is_chronological_and_ends_today():
    days = 14
    series = daily_series(db=None, entity_type="voucher", entity_ids=[], days=days)

    dates = [point.date for point in series]
    assert dates == sorted(dates), "chart data must be oldest-first"

    today = datetime.now(timezone.utc).date()
    assert series[-1].date == today.isoformat()
    assert series[0].date == (today - timedelta(days=days)).isoformat()


@pytest.mark.parametrize("entity_type", VENDOR_ENTITY_TYPES)
def test_vendor_entity_types_are_accepted(entity_type):
    _validate_entity_type(entity_type)


def test_vendor_cannot_request_banner_analytics():
    # Banners are marketplace furniture; no store owns one, so allowing this
    # would show a vendor marketplace-wide numbers.
    assert "banner" in ENTITY_TYPES
    assert "banner" not in VENDOR_ENTITY_TYPES

    with pytest.raises(HTTPException) as exc_info:
        _validate_entity_type("banner")
    assert exc_info.value.status_code == 400


def test_vendor_rejects_unknown_entity_type():
    with pytest.raises(HTTPException) as exc_info:
        _validate_entity_type("nonsense")
    assert exc_info.value.status_code == 400
