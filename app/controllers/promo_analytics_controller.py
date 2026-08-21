"""Analytics event ingestion and aggregation."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import Session

from app.models import PromoAnalyticsEvent, User
from app.schemas.promo_analytics import (
    PromoAnalyticsBatchCreate,
    PromoAnalyticsBatchRead,
)


def record_promo_analytics_batch(
    db: Session,
    user: User | None,
    payload: PromoAnalyticsBatchCreate,
) -> PromoAnalyticsBatchRead:
    """Ingest a batch of promo analytics events."""
    accepted = 0
    now = datetime.now(timezone.utc)

    for event_data in payload.events:
        if event_data.entity_type not in {"campaign", "voucher", "banner"}:
            continue
        if event_data.event_type not in {"impression", "click", "conversion"}:
            continue

        event = PromoAnalyticsEvent(
            entity_type=event_data.entity_type,
            entity_id=event_data.entity_id,
            event_type=event_data.event_type,
            user_id=user.id if user and getattr(user, "analytics_enabled", True) else None,
            session_id=payload.session_id,
            source_screen=event_data.source_screen,
            metadata_json=event_data.metadata or {},
            created_at=event_data.occurred_at or now,
        )
        db.add(event)
        accepted += 1

    if accepted > 0:
        db.commit()

    return PromoAnalyticsBatchRead(accepted=accepted)
