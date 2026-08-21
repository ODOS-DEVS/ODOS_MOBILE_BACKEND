"""Promotional analytics endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.promo_analytics_controller import record_promo_analytics_batch
from app.core.auth import get_optional_current_user
from app.core.database import get_db
from app.schemas.promo_analytics import (
    PromoAnalyticsBatchCreate,
    PromoAnalyticsBatchRead,
)

router = APIRouter(prefix="/promotions", tags=["promotions"])


@router.post("/events/batch", response_model=PromoAnalyticsBatchRead)
def post_promo_analytics_batch(
    payload: PromoAnalyticsBatchCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_optional_current_user),
):
    """Ingest batched promo analytics events (impressions, clicks, conversions)."""
    return record_promo_analytics_batch(db, current_user, payload)
