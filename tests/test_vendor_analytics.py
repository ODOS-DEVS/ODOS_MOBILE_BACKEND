"""Unit tests for vendor analytics period parsing and daily point bucketing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.controllers.vendor_controller import (
    VENDOR_ANALYTICS_ORDER_STATUSES,
    _build_vendor_daily_points,
    _parse_analytics_period,
)


def _order(**overrides):
    base = {
        "placed_at": None,
        "created_at": datetime.now(timezone.utc),
        "status": "delivered",
        "total_amount": 100.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.parametrize(
    "period,expected_days",
    [("7d", 7), ("30d", 30), ("90d", 90), (None, 30), ("", 30), ("30D", 30)],
)
def test_parse_analytics_period_accepts_supported_values(period, expected_days):
    normalized, days = _parse_analytics_period(period)
    assert days == expected_days
    assert normalized in {"7d", "30d", "90d"}


def test_parse_analytics_period_rejects_unsupported_value():
    with pytest.raises(HTTPException) as exc_info:
        _parse_analytics_period("365d")
    assert exc_info.value.status_code == 400


def test_build_daily_points_creates_one_bucket_per_day():
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = today - timedelta(days=6)

    points = _build_vendor_daily_points([], period_start=period_start, days=7)

    assert len(points) == 7
    assert all(point.sales == 0 and point.orders == 0 for point in points)
    # Points must be sorted chronologically, oldest first.
    assert points[0].date == period_start.date().isoformat()
    assert points[-1].date == today.date().isoformat()


def test_build_daily_points_aggregates_sales_for_matching_day():
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    period_start = today - timedelta(days=6)

    assert "delivered" in VENDOR_ANALYTICS_ORDER_STATUSES
    orders = [
        _order(placed_at=today, total_amount=50.0, status="delivered"),
        _order(placed_at=today, total_amount=25.0, status="confirmed"),
        _order(placed_at=today - timedelta(days=2), total_amount=10.0, status="delivered"),
        # Excluded: outside the requested window.
        _order(placed_at=today - timedelta(days=30), total_amount=999.0, status="delivered"),
        # Excluded: not a countable sales status.
        _order(placed_at=today, total_amount=500.0, status="cancelled"),
    ]

    points = _build_vendor_daily_points(orders, period_start=period_start, days=7)
    by_date = {point.date: point for point in points}

    assert by_date[today.date().isoformat()].sales == 75.0
    assert by_date[today.date().isoformat()].orders == 2
    assert by_date[(today - timedelta(days=2)).date().isoformat()].sales == 10.0
    assert sum(point.sales for point in points) == 85.0
