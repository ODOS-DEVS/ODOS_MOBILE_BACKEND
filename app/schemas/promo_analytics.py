"""Schemas for promotional analytics endpoints."""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class PromoAnalyticsEventCreate(BaseModel):
    """Single analytics event from client."""

    entity_type: str = Field(..., description="campaign|voucher|banner")
    entity_id: str = Field(..., description="UUID of campaign/voucher/banner")
    event_type: str = Field(..., description="impression|click|conversion")
    source_screen: str | None = None
    metadata: dict | None = None
    occurred_at: datetime | None = None


class PromoAnalyticsBatchCreate(BaseModel):
    """Batch of analytics events."""

    session_id: str | None = None
    events: list[PromoAnalyticsEventCreate] = Field(..., max_length=50)


class PromoAnalyticsBatchRead(BaseModel):
    """Response for batch ingestion."""

    accepted: int


class PromoAnalyticsDatapoint(BaseModel):
    """Single day's data for time-series."""

    date: str
    impressions: int
    clicks: int
    conversions: int
    click_through_rate: float | None = None
    conversion_rate: float | None = None


class PromoAnalyticsTimeseriesRead(BaseModel):
    """Time-series analytics for entity."""

    entity_type: str
    entity_id: str | None = None
    data: list[PromoAnalyticsDatapoint]
    total_impressions: int
    total_clicks: int
    total_conversions: int


class PromoAnalyticsLeaderboardItem(BaseModel):
    """Single item in leaderboard."""

    entity_id: str
    entity_label: str
    impressions: int
    clicks: int
    conversions: int
    click_through_rate: float
    conversion_rate: float


class PromoAnalyticsLeaderboardRead(BaseModel):
    """Top-performing campaigns/vouchers."""

    entity_type: str
    items: list[PromoAnalyticsLeaderboardItem]
