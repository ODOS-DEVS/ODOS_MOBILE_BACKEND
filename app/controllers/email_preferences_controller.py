"""Email preferences controller."""

from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.models import User
from app.core.auth import require_auth
from app.services.email_preferences_service import EmailPreferencesService


class EmailPreferencesUpdate(BaseModel):
    """Email preferences update request."""
    promotional_emails: bool | None = None
    order_updates: bool | None = None
    loyalty_rewards: bool | None = None
    cart_reminders: bool | None = None
    weekly_newsletter: bool | None = None
    product_recommendations: bool | None = None
    exclusive_offers: bool | None = None


def get_email_preferences(
    db: Session,
    current_user: User,
) -> dict:
    """Get current user's email preferences."""
    require_auth(current_user)
    return EmailPreferencesService.get_preferences(db, str(current_user.id))


def update_email_preferences(
    db: Session,
    current_user: User,
    updates: EmailPreferencesUpdate,
) -> dict:
    """Update current user's email preferences."""
    require_auth(current_user)

    # Convert to dict and remove None values
    update_dict = {k: v for k, v in updates.dict().items() if v is not None}

    return EmailPreferencesService.update_preferences(
        db,
        str(current_user.id),
        update_dict,
    )
