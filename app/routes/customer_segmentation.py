"""Customer segmentation routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.controllers.customer_segmentation_controller import (
    get_customer_profile,
    get_segment_members,
    get_segment_statistics,
    get_all_segments_overview,
    get_churn_risk_users,
    export_segment_for_campaign,
)
from app.models import User

router = APIRouter(prefix="/customer-segmentation", tags=["customer-segmentation"])


@router.get("/user/{user_id}")
def get_user_profile(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get customer profile with segmentation data."""
    return get_customer_profile(db, current_user, user_id)


@router.get("/segment/{segment}")
def get_segment_users(
    segment: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=10000),
):
    """Get all members of a customer segment."""
    return get_segment_members(db, current_user, segment, limit=limit)


@router.get("/segment/{segment}/stats")
def get_segment_stats(
    segment: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get statistics for a segment."""
    return get_segment_statistics(db, current_user, segment)


@router.get("/overview")
def get_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get overview statistics for all segments."""
    return get_all_segments_overview(db, current_user)


@router.get("/churn-risk")
def get_churn_risk(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    threshold: float = Query(default=0.7, ge=0, le=1),
    limit: int = Query(default=100, ge=1, le=10000),
):
    """Get users at high risk of churning."""
    return get_churn_risk_users(db, current_user, threshold=threshold, limit=limit)


@router.get("/segment/{segment}/export")
def export_segment(
    segment: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Export segment data for email campaign."""
    return export_segment_for_campaign(db, current_user, segment)
