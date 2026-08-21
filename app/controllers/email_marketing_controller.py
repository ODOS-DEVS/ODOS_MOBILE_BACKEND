"""Email marketing controller."""

from sqlalchemy.orm import Session

from app.models import User
from app.core.auth import require_admin
from app.services.email_marketing_service import (
    SendGridEmailService,
    EmailSegmentationService,
    EmailCampaignType,
    EmailTemplate,
    EmailRecipient,
    EmailContent,
)


def send_promotional_email(
    db: Session,
    current_user: User,
    recipient_emails: list[str],
    subject: str,
    template: str,
    variables: dict,
) -> dict:
    """Send promotional email to users."""
    require_admin(current_user)

    try:
        template_enum = EmailTemplate[template.upper()]
    except KeyError:
        return {
            "success": False,
            "message": f"Invalid template: {template}",
        }

    email_service = SendGridEmailService()
    recipients = [
        EmailRecipient(email=email, name="Customer") for email in recipient_emails
    ]

    content = EmailContent(
        subject=subject,
        template=template_enum,
        variables=variables,
    )

    successful, failed = email_service.send_batch_emails(
        recipients,
        content,
        EmailCampaignType.PROMOTIONAL,
    )

    return {
        "success": True,
        "message": f"Sent {successful} emails, {failed} failed",
        "successful": successful,
        "failed": failed,
        "total": len(recipient_emails),
    }


def send_abandoned_cart_campaign(
    db: Session,
    current_user: User,
    hours_since_abandonment: int = 24,
) -> dict:
    """Send abandoned cart reminder campaign."""
    require_admin(current_user)

    email_service = SendGridEmailService()
    recipients = EmailSegmentationService.get_abandoned_cart_users(
        db,
        hours_since_abandonment=hours_since_abandonment,
    )

    if not recipients:
        return {
            "success": True,
            "message": "No abandoned carts found",
            "sent": 0,
        }

    content = EmailContent(
        subject="Complete Your Purchase - Special Offer Inside!",
        template=EmailTemplate.ABANDONED_CART,
        variables={
            "cart_value": "0.00",
            "items_count": "items",
            "cart_link": "https://odos.com",
        },
    )

    successful, failed = email_service.send_batch_emails(
        recipients,
        content,
        EmailCampaignType.CART_ABANDONMENT,
    )

    return {
        "success": True,
        "message": f"Abandoned cart campaign: {successful} sent, {failed} failed",
        "sent": successful,
        "failed": failed,
    }


def send_reengagement_campaign(
    db: Session,
    current_user: User,
    days_inactive: int = 30,
) -> dict:
    """Send reengagement campaign to inactive users."""
    require_admin(current_user)

    email_service = SendGridEmailService()
    recipients = EmailSegmentationService.get_inactive_users(
        db,
        days_inactive=days_inactive,
    )

    if not recipients:
        return {
            "success": True,
            "message": "No inactive users found",
            "sent": 0,
        }

    content = EmailContent(
        subject="We Miss You! Come Back for Exclusive Offers",
        template=EmailTemplate.REENGAGEMENT,
        variables={
            "days_inactive": str(days_inactive),
            "incentive": "10% off",
            "shop_link": "https://odos.com",
        },
    )

    successful, failed = email_service.send_batch_emails(
        recipients,
        content,
        EmailCampaignType.RETENTION,
    )

    return {
        "success": True,
        "message": f"Reengagement campaign: {successful} sent, {failed} failed",
        "sent": successful,
        "failed": failed,
    }


def send_welcome_campaign(
    db: Session,
    current_user: User,
    days_since_signup: int = 7,
) -> dict:
    """Send welcome emails to new users."""
    require_admin(current_user)

    email_service = SendGridEmailService()
    recipients = EmailSegmentationService.get_new_users(
        db,
        days_since_signup=days_since_signup,
    )

    if not recipients:
        return {
            "success": True,
            "message": "No new users found",
            "sent": 0,
        }

    content = EmailContent(
        subject="Welcome to ODOS - Your Shopping Destination!",
        template=EmailTemplate.WELCOME,
        variables={
            "app_link": "https://odos.com",
        },
    )

    successful, failed = email_service.send_batch_emails(
        recipients,
        content,
        EmailCampaignType.WELCOME,
    )

    return {
        "success": True,
        "message": f"Welcome campaign: {successful} sent, {failed} failed",
        "sent": successful,
        "failed": failed,
    }


def send_loyalty_reward_notification(
    db: Session,
    current_user: User,
    user_id: str,
    points_earned: int,
    tier: str,
) -> dict:
    """Send loyalty reward notification."""
    from app.models import User as UserModel

    user = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not user:
        return {
            "success": False,
            "message": "User not found",
        }

    email_service = SendGridEmailService()
    recipient = EmailRecipient(
        email=user.email,
        name=user.full_name or "Customer",
        user_id=user_id,
    )

    content = EmailContent(
        subject=f"You Earned {points_earned} Loyalty Points!",
        template=EmailTemplate.LOYALTY_REWARD,
        variables={
            "points_earned": str(points_earned),
            "tier": tier,
            "loyalty_link": "https://odos.com/loyalty",
        },
    )

    success = email_service.send_email(
        recipient,
        content,
        EmailCampaignType.PROMOTIONAL,
    )

    return {
        "success": success,
        "message": "Loyalty notification sent" if success else "Failed to send email",
    }


def get_email_templates(db: Session, current_user: User) -> dict:
    """Get available email templates."""
    require_admin(current_user)

    templates = [
        {
            "id": "welcome",
            "name": "Welcome Email",
            "description": "Send welcome email to new users",
            "variables": ["name", "app_link"],
        },
        {
            "id": "promo_offer",
            "name": "Promotional Offer",
            "description": "Send promotional offers",
            "variables": ["offer_name", "discount", "expiry_date", "offer_link"],
        },
        {
            "id": "abandoned_cart",
            "name": "Abandoned Cart",
            "description": "Remind users about abandoned carts",
            "variables": ["cart_value", "items_count", "cart_link"],
        },
        {
            "id": "order_confirmation",
            "name": "Order Confirmation",
            "description": "Confirm order placement",
            "variables": ["order_id", "amount", "order_link"],
        },
        {
            "id": "delivery_update",
            "name": "Delivery Update",
            "description": "Notify about delivery status",
            "variables": ["delivery_status", "eta", "tracking_link"],
        },
        {
            "id": "loyalty_reward",
            "name": "Loyalty Reward",
            "description": "Notify about earned loyalty points",
            "variables": ["points_earned", "tier", "loyalty_link"],
        },
        {
            "id": "reengagement",
            "name": "Reengagement",
            "description": "Win back inactive users",
            "variables": ["days_inactive", "incentive", "shop_link"],
        },
    ]

    return {
        "templates": templates,
        "total": len(templates),
    }
