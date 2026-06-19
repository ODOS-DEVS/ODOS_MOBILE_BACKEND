from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_optional_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.recommendation import RecommendationFeedRead
from app.services.recommendation_service import (
    get_for_you_recommendations,
    get_similar_product_recommendations,
)

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/for-you", response_model=RecommendationFeedRead)
def get_for_you_feed(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
    limit: int = 12,
):
    safe_limit = max(1, min(limit, 48))
    return get_for_you_recommendations(db, current_user, limit=safe_limit)


@router.get("/similar/{product_id}", response_model=RecommendationFeedRead)
def get_similar_feed(
    product_id: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
    limit: int = 8,
):
    safe_limit = max(1, min(limit, 24))
    return get_similar_product_recommendations(
        db,
        current_user,
        product_id.strip(),
        limit=safe_limit,
    )
