"""Email preferences service for managing user email subscriptions."""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


class EmailPreferencesService:
    """Service for managing user email preferences."""

    # Default preferences
    DEFAULT_PREFERENCES = {
        "promotional_emails": False,
        "order_updates": True,
        "loyalty_rewards": True,
        "cart_reminders": False,
        "weekly_newsletter": False,
        "product_recommendations": False,
        "exclusive_offers": False,
    }

    @staticmethod
    def get_preferences(db: Session, user_id: str) -> dict:
        """Get email preferences for a user."""
        user = db.scalar(select(User).where(User.id == user_id))
        if not user:
            return {}

        # Get preferences from user metadata or use defaults
        prefs = getattr(user, "email_preferences", None) or EmailPreferencesService.DEFAULT_PREFERENCES.copy()

        return {
            "user_id": str(user.id),
            **prefs,
            "updated_at": getattr(user, "email_preferences_updated_at", datetime.now(timezone.utc)).isoformat(),
        }

    @staticmethod
    def update_preferences(
        db: Session,
        user_id: str,
        updates: dict,
    ) -> dict:
        """Update email preferences for a user."""
        user = db.scalar(select(User).where(User.id == user_id))
        if not user:
            return {}

        # Get current preferences
        current_prefs = EmailPreferencesService.get_preferences(db, user_id)
        current_prefs.pop("user_id", None)
        current_prefs.pop("updated_at", None)

        # Update with new values
        new_prefs = {**current_prefs, **updates}

        # Store updated preferences
        if not hasattr(user, "email_preferences"):
            user.email_preferences = new_prefs
        else:
            user.email_preferences.update(updates)

        user.email_preferences_updated_at = datetime.now(timezone.utc)
        db.commit()

        return EmailPreferencesService.get_preferences(db, user_id)

    @staticmethod
    def should_send_email(
        db: Session,
        user_id: str,
        email_type: str,
    ) -> bool:
        """Check if email should be sent to user based on preferences."""
        preferences = EmailPreferencesService.get_preferences(db, user_id)

        # Map email types to preference keys
        type_mapping = {
            "promotional": "promotional_emails",
            "order_update": "order_updates",
            "loyalty": "loyalty_rewards",
            "cart_reminder": "cart_reminders",
            "newsletter": "weekly_newsletter",
            "recommendation": "product_recommendations",
            "exclusive_offer": "exclusive_offers",
            "transactional": True,  # Always send transactional
        }

        pref_key = type_mapping.get(email_type, email_type)

        # Transactional emails always go through
        if pref_key is True:
            return True

        return preferences.get(pref_key, EmailPreferencesService.DEFAULT_PREFERENCES.get(pref_key, False))

    @staticmethod
    def get_segment_for_campaign(
        db: Session,
        campaign_type: str,
        limit: int = 10000,
    ) -> list[str]:
        """Get list of user emails subscribed to a campaign type."""
        email_type_mapping = {
            "promotional": "promotional_emails",
            "order_update": "order_updates",
            "loyalty": "loyalty_rewards",
            "cart_reminder": "cart_reminders",
            "newsletter": "weekly_newsletter",
            "recommendation": "product_recommendations",
            "exclusive_offer": "exclusive_offers",
        }

        pref_key = email_type_mapping.get(campaign_type, campaign_type)

        # In production, this would query for users where email_preferences[pref_key] is True
        # For now, return users with default preferences
        users = db.scalars(
            select(User).where(User.role == "customer", User.status == "active").limit(limit)
        ).all()

        emails = []
        for user in users:
            if EmailPreferencesService.should_send_email(db, str(user.id), campaign_type):
                emails.append(user.email)

        return emails
