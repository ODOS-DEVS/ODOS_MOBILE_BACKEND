"""Email marketing routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.core.auth import get_current_user
from app.core.database import get_db
from app.controllers.email_marketing_controller import (
    send_promotional_email,
    send_abandoned_cart_campaign,
    send_reengagement_campaign,
    send_welcome_campaign,
    send_loyalty_reward_notification,
    get_email_templates,
)
from app.models import User

router = APIRouter(prefix="/email-marketing", tags=["email-marketing"])


class SendEmailRequest(BaseModel):
    """Send email request."""
    recipient_emails: list[str]
    subject: str
    template: str
    variables: dict


class SendLoyaltyNotificationRequest(BaseModel):
    """Send loyalty notification request."""
    user_id: str
    points_earned: int
    tier: str


@router.post("/send")
def send_email(
    request: SendEmailRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send promotional email to users."""
    return send_promotional_email(
        db,
        current_user,
        request.recipient_emails,
        request.subject,
        request.template,
        request.variables,
    )


@router.post("/campaigns/abandoned-cart")
def abandoned_cart_campaign(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    hours: int = Query(default=24, ge=1),
):
    """Send abandoned cart reminder campaign."""
    return send_abandoned_cart_campaign(
        db,
        current_user,
        hours_since_abandonment=hours,
    )


@router.post("/campaigns/reengagement")
def reengagement_campaign(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    days: int = Query(default=30, ge=1),
):
    """Send reengagement campaign to inactive users."""
    return send_reengagement_campaign(
        db,
        current_user,
        days_inactive=days,
    )


@router.post("/campaigns/welcome")
def welcome_campaign(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    days: int = Query(default=7, ge=0),
):
    """Send welcome emails to new users."""
    return send_welcome_campaign(
        db,
        current_user,
        days_since_signup=days,
    )


@router.post("/notifications/loyalty")
def send_loyalty_notification(
    request: SendLoyaltyNotificationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send loyalty reward notification."""
    return send_loyalty_reward_notification(
        db,
        current_user,
        request.user_id,
        request.points_earned,
        request.tier,
    )


@router.get("/templates")
def list_email_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get available email templates."""
    return get_email_templates(db, current_user)
