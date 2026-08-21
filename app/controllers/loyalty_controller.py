"""Loyalty program controller."""

from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.models import User
from app.services.loyalty_service import (
    get_or_create_loyalty_account,
    get_account_with_benefits,
    get_loyalty_transactions,
    redeem_points,
    award_bonus_points,
)


def get_loyalty_account(db: Session, current_user: User) -> dict:
    """Get user's loyalty account with benefits."""
    require_user(current_user)

    account = get_or_create_loyalty_account(db, current_user)
    return get_account_with_benefits(db, str(current_user.id))


def get_loyalty_history(
    db: Session,
    current_user: User,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    """Get user's loyalty transaction history."""
    require_user(current_user)

    transactions = get_loyalty_transactions(
        db,
        str(current_user.id),
        limit=limit,
        offset=offset,
    )

    return {
        "transactions": transactions,
        "count": len(transactions),
        "limit": limit,
        "offset": offset,
    }


def redeem_loyalty_points(
    db: Session,
    current_user: User,
    points_to_redeem: int,
) -> dict:
    """Redeem loyalty points."""
    require_user(current_user)

    if points_to_redeem <= 0:
        return {
            "success": False,
            "message": "Points must be greater than 0",
        }

    success = redeem_points(
        db,
        str(current_user.id),
        points_to_redeem,
        reason="Customer redemption",
    )

    if success:
        account = get_account_with_benefits(db, str(current_user.id))
        return {
            "success": True,
            "message": f"Successfully redeemed {points_to_redeem} points",
            "account": account,
            "discount_amount_ghs": points_to_redeem / 100,  # 100 points = 1 GHS
        }
    else:
        return {
            "success": False,
            "message": "Insufficient loyalty points",
        }


def award_bonus(
    db: Session,
    current_user: User,
    user_id: str,
    points: int,
    reason: str,
) -> dict:
    """Award bonus points (admin function)."""
    from app.core.admin_permissions import require_super_admin

    require_super_admin(current_user)

    success = award_bonus_points(db, user_id, points, reason)

    if success:
        return {
            "success": True,
            "message": f"Awarded {points} bonus points",
        }
    else:
        return {
            "success": False,
            "message": "Failed to award bonus points",
        }
