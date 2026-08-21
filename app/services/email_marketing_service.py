"""Email marketing service with SendGrid integration."""

import os
import json
from datetime import datetime, timezone
from typing import Optional
from enum import Enum
from dataclasses import dataclass
import logging

from sqlalchemy import select, and_
from sqlalchemy.orm import Session

from app.models import User

logger = logging.getLogger(__name__)


class EmailCampaignType(str, Enum):
    """Email campaign types."""
    PROMOTIONAL = "promotional"
    TRANSACTIONAL = "transactional"
    NEWSLETTER = "newsletter"
    RETENTION = "retention"
    WELCOME = "welcome"
    CART_ABANDONMENT = "cart_abandonment"


class EmailTemplate(str, Enum):
    """Pre-configured email templates."""
    WELCOME = "welcome"
    PROMO_OFFER = "promo_offer"
    ABANDONED_CART = "abandoned_cart"
    ORDER_CONFIRMATION = "order_confirmation"
    DELIVERY_UPDATE = "delivery_update"
    LOYALTY_REWARD = "loyalty_reward"
    REENGAGEMENT = "reengagement"


@dataclass
class EmailRecipient:
    """Email recipient data."""
    email: str
    name: str
    user_id: Optional[str] = None
    metadata: Optional[dict] = None


@dataclass
class EmailContent:
    """Email content structure."""
    subject: str
    template: EmailTemplate
    variables: dict


class SendGridEmailService:
    """SendGrid-based email marketing service."""

    def __init__(self):
        """Initialize SendGrid client."""
        self.api_key = os.getenv("SENDGRID_API_KEY")
        self.from_email = os.getenv("SENDGRID_FROM_EMAIL", "noreply@odos.com")
        self.enabled = bool(self.api_key)

        if self.enabled:
            try:
                from sendgrid import SendGridAPIClient
                from sendgrid.helpers.mail import Mail
                self.SendGridAPIClient = SendGridAPIClient
                self.Mail = Mail
            except ImportError:
                logger.warning("SendGrid not installed, email marketing disabled")
                self.enabled = False

    def send_email(
        self,
        recipient: EmailRecipient,
        content: EmailContent,
        campaign_type: EmailCampaignType = EmailCampaignType.PROMOTIONAL,
    ) -> bool:
        """Send single email."""
        if not self.enabled:
            logger.info(f"Email disabled, skipping: {recipient.email}")
            return False

        try:
            mail = self.Mail(
                from_email=self.from_email,
                to_emails=[(recipient.email, recipient.name)],
                subject=content.subject,
            )

            # Render template with variables
            html_content = self._render_template(content.template, content.variables)
            mail.html_content = html_content

            # Add custom headers
            mail.extra_headers = {
                "X-ODOS-Campaign-Type": campaign_type.value,
                "X-ODOS-User-ID": recipient.user_id or "anonymous",
            }

            # Send via SendGrid
            sg = self.SendGridAPIClient(self.api_key)
            response = sg.send(mail)

            return 200 <= response.status_code < 300
        except Exception as e:
            logger.error(f"Failed to send email to {recipient.email}: {e}")
            return False

    def send_batch_emails(
        self,
        recipients: list[EmailRecipient],
        content: EmailContent,
        campaign_type: EmailCampaignType = EmailCampaignType.PROMOTIONAL,
    ) -> tuple[int, int]:
        """Send emails to multiple recipients.

        Returns: (successful, failed)
        """
        successful = 0
        failed = 0

        for recipient in recipients:
            if self.send_email(recipient, content, campaign_type):
                successful += 1
            else:
                failed += 1

        return successful, failed

    def _render_template(self, template: EmailTemplate, variables: dict) -> str:
        """Render email template with variables."""
        templates = {
            EmailTemplate.WELCOME: self._render_welcome(variables),
            EmailTemplate.PROMO_OFFER: self._render_promo_offer(variables),
            EmailTemplate.ABANDONED_CART: self._render_abandoned_cart(variables),
            EmailTemplate.ORDER_CONFIRMATION: self._render_order_confirmation(variables),
            EmailTemplate.DELIVERY_UPDATE: self._render_delivery_update(variables),
            EmailTemplate.LOYALTY_REWARD: self._render_loyalty_reward(variables),
            EmailTemplate.REENGAGEMENT: self._render_reengagement(variables),
        }
        return templates.get(template, "<p>Email template not found</p>")

    @staticmethod
    def _render_welcome(vars: dict) -> str:
        """Render welcome email."""
        name = vars.get("name", "Customer")
        return f"""
        <h1>Welcome to ODOS, {name}!</h1>
        <p>We're excited to have you on board. Start shopping and enjoy exclusive offers.</p>
        <a href="{vars.get('app_link', 'https://odos.com')}">Start Shopping</a>
        """

    @staticmethod
    def _render_promo_offer(vars: dict) -> str:
        """Render promotional offer email."""
        offer_name = vars.get("offer_name", "Special Offer")
        discount = vars.get("discount", "20%")
        expiry = vars.get("expiry_date", "")
        return f"""
        <h2>{offer_name}</h2>
        <p>Get {discount} off on selected items!</p>
        {f'<p>Expires: {expiry}</p>' if expiry else ''}
        <a href="{vars.get('offer_link', 'https://odos.com')}">Claim Offer</a>
        """

    @staticmethod
    def _render_abandoned_cart(vars: dict) -> str:
        """Render abandoned cart reminder."""
        cart_value = vars.get("cart_value", "0.00")
        items_count = vars.get("items_count", "items")
        return f"""
        <h2>You left something behind!</h2>
        <p>Complete your purchase with {items_count} items worth GHS {cart_value}</p>
        <a href="{vars.get('cart_link', 'https://odos.com')}">Complete Purchase</a>
        """

    @staticmethod
    def _render_order_confirmation(vars: dict) -> str:
        """Render order confirmation email."""
        order_id = vars.get("order_id", "")
        amount = vars.get("amount", "0.00")
        return f"""
        <h2>Order Confirmed!</h2>
        <p>Order ID: {order_id}</p>
        <p>Amount: GHS {amount}</p>
        <p>Thank you for your purchase!</p>
        <a href="{vars.get('order_link', 'https://odos.com')}">Track Order</a>
        """

    @staticmethod
    def _render_delivery_update(vars: dict) -> str:
        """Render delivery update email."""
        status = vars.get("delivery_status", "In Transit")
        eta = vars.get("eta", "")
        return f"""
        <h2>Delivery Update</h2>
        <p>Status: {status}</p>
        {f'<p>Estimated Delivery: {eta}</p>' if eta else ''}
        <a href="{vars.get('tracking_link', 'https://odos.com')}">Track Delivery</a>
        """

    @staticmethod
    def _render_loyalty_reward(vars: dict) -> str:
        """Render loyalty reward email."""
        points = vars.get("points_earned", "0")
        tier = vars.get("tier", "Bronze")
        return f"""
        <h2>Loyalty Reward!</h2>
        <p>You earned {points} loyalty points!</p>
        <p>Current tier: {tier}</p>
        <a href="{vars.get('loyalty_link', 'https://odos.com')}">View Rewards</a>
        """

    @staticmethod
    def _render_reengagement(vars: dict) -> str:
        """Render reengagement email."""
        days_inactive = vars.get("days_inactive", "30")
        incentive = vars.get("incentive", "10% off")
        return f"""
        <h2>We miss you!</h2>
        <p>It's been {days_inactive} days since your last purchase.</p>
        <p>Come back and get {incentive} on your next order!</p>
        <a href="{vars.get('shop_link', 'https://odos.com')}">Shop Now</a>
        """


class EmailSegmentationService:
    """Service for creating user segments for targeted campaigns."""

    @staticmethod
    def get_abandoned_cart_users(
        db: Session,
        hours_since_abandonment: int = 24,
    ) -> list[EmailRecipient]:
        """Get users with abandoned carts."""
        # This would typically join Cart/Order tables
        # For now, returning empty list - implement based on your cart model
        return []

    @staticmethod
    def get_inactive_users(
        db: Session,
        days_inactive: int = 30,
    ) -> list[EmailRecipient]:
        """Get inactive users for reengagement."""
        # Filter users with no purchases in X days
        cutoff_date = datetime.now(timezone.utc)

        users = db.scalars(
            select(User).where(
                User.status == "active",
                # Add condition for last_purchase_date < cutoff_date
                # Depends on your User model structure
            )
        ).all()

        return [
            EmailRecipient(
                email=user.email,
                name=user.full_name or "Customer",
                user_id=str(user.id),
            )
            for user in users
        ]

    @staticmethod
    def get_high_value_customers(
        db: Session,
        min_lifetime_spend: float = 1000.0,
    ) -> list[EmailRecipient]:
        """Get high-value customers for VIP campaigns."""
        # Filter users by lifetime spend from loyalty or order history
        # Depends on your data model
        return []

    @staticmethod
    def get_new_users(
        db: Session,
        days_since_signup: int = 7,
    ) -> list[EmailRecipient]:
        """Get new users for welcome campaigns."""
        cutoff_date = datetime.now(timezone.utc)

        users = db.scalars(
            select(User).where(
                User.status == "active",
                # Add condition for created_at >= cutoff_date
            )
        ).all()

        return [
            EmailRecipient(
                email=user.email,
                name=user.full_name or "Customer",
                user_id=str(user.id),
            )
            for user in users
        ]

    @staticmethod
    def get_loyalty_tier_users(
        db: Session,
        tier: str,
    ) -> list[EmailRecipient]:
        """Get users by loyalty tier."""
        # Join with LoyaltyAccount model
        # Depends on your loyalty model
        return []
