"""Loyalty rewards API routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.controllers.loyalty_controller import (
    get_loyalty_account,
    get_loyalty_history,
    redeem_loyalty_points,
    award_bonus,
)
from app.models import User

router = APIRouter(prefix="/loyalty", tags=["loyalty"])


@router.get("/account")
def get_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get loyalty account with tier, points, and benefits."""
    return get_loyalty_account(db, current_user)


@router.get("/history")
def get_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Get loyalty transaction history."""
    return get_loyalty_history(db, current_user, limit=limit, offset=offset)


@router.post("/redeem")
def redeem_points(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    points: int = Query(..., ge=1, description="Points to redeem"),
):
    """Redeem loyalty points."""
    return redeem_loyalty_points(db, current_user, points)


@router.post("/admin/award")
def award_bonus_points(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    user_id: str = Query(..., description="User ID to award points to"),
    points: int = Query(..., ge=1, description="Bonus points to award"),
    reason: str = Query(..., description="Reason for bonus"),
):
    """Award bonus points (admin only)."""
    return award_bonus(db, current_user, user_id, points, reason)
