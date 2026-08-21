"""Loyalty rewards service for managing points and tiers."""

from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LoyaltyAccount, LoyaltyTransaction, LoyaltyTierBenefit, User, Order


# Tier configuration (can be moved to database later)
LOYALTY_TIERS = {
    "bronze": {
        "min_spend": 0,
        "discount_percent": 0,
        "points_multiplier": 1.0,
        "free_shipping_threshold": None,
        "birthday_bonus": 0,
    },
    "silver": {
        "min_spend": 1000,  # 1000 GHS lifetime spend
        "discount_percent": 5,
        "points_multiplier": 1.25,
        "free_shipping_threshold": 50,
        "birthday_bonus": 100,
    },
    "gold": {
        "min_spend": 5000,  # 5000 GHS lifetime spend
        "discount_percent": 10,
        "points_multiplier": 1.5,
        "free_shipping_threshold": 30,
        "birthday_bonus": 250,
    },
}

POINTS_PER_GHS = 1.0  # 1 point per 1 GHS spent


def get_or_create_loyalty_account(db: Session, user: User) -> LoyaltyAccount:
    """Get or create a loyalty account for a user."""
    account = db.scalar(
        select(LoyaltyAccount).where(LoyaltyAccount.user_id == user.id)
    )

    if not account:
        account = LoyaltyAccount(
            user_id=user.id,
            tier_level="bronze",
            total_points=0,
            lifetime_spend=0.0,
        )
        db.add(account)
        db.commit()
        db.refresh(account)

    return account


def calculate_tier(lifetime_spend: float) -> str:
    """Calculate tier based on lifetime spend."""
    if lifetime_spend >= LOYALTY_TIERS["gold"]["min_spend"]:
        return "gold"
    elif lifetime_spend >= LOYALTY_TIERS["silver"]["min_spend"]:
        return "silver"
    else:
        return "bronze"


def earn_points_from_order(
    db: Session,
    user_id: str,
    order: Order,
    bonus_multiplier: float = 1.0,
) -> int:
    """Earn loyalty points from an order purchase."""
    account = db.scalar(
        select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id)
    )

    if not account:
        account = get_or_create_loyalty_account(
            db,
            db.scalar(select(User).where(User.id == user_id))
        )

    # Calculate points
    base_points = int(order.total_amount * POINTS_PER_GHS)
    tier_multiplier = LOYALTY_TIERS[account.tier_level]["points_multiplier"]
    points_earned = int(base_points * tier_multiplier * bonus_multiplier)

    # Update account
    account.total_points += points_earned
    account.lifetime_spend += order.total_amount

    # Check for tier upgrade
    old_tier = account.tier_level
    new_tier = calculate_tier(account.lifetime_spend)
    if new_tier != old_tier:
        account.tier_level = new_tier
        account.tier_upgraded_at = datetime.now(timezone.utc)

    # Record transaction
    transaction = LoyaltyTransaction(
        account_id=account.id,
        transaction_type="earn",
        points_amount=points_earned,
        reason=f"Purchase from order {order.id}",
        order_id=order.id,
        metadata={
            "base_points": base_points,
            "tier_multiplier": tier_multiplier,
            "tier_level": old_tier,
            "tier_upgraded": new_tier != old_tier,
        },
    )

    db.add(transaction)
    db.commit()
    db.refresh(account)

    return points_earned


def redeem_points(
    db: Session,
    user_id: str,
    points_to_redeem: int,
    reason: str = "Checkout redemption",
) -> bool:
    """Redeem loyalty points (reduce balance)."""
    account = db.scalar(
        select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id)
    )

    if not account or account.total_points < points_to_redeem:
        return False

    # Update account
    account.total_points -= points_to_redeem

    # Record transaction
    transaction = LoyaltyTransaction(
        account_id=account.id,
        transaction_type="redeem",
        points_amount=-points_to_redeem,
        reason=reason,
    )

    db.add(transaction)
    db.commit()
    db.refresh(account)

    return True


def award_bonus_points(
    db: Session,
    user_id: str,
    points: int,
    reason: str,
) -> bool:
    """Award bonus points (admin/system function)."""
    account = db.scalar(
        select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id)
    )

    if not account:
        user = db.scalar(select(User).where(User.id == user_id))
        if not user:
            return False
        account = get_or_create_loyalty_account(db, user)

    account.total_points += points

    transaction = LoyaltyTransaction(
        account_id=account.id,
        transaction_type="bonus",
        points_amount=points,
        reason=reason,
    )

    db.add(transaction)
    db.commit()
    db.refresh(account)

    return True


def get_loyalty_benefits(tier_level: str) -> dict:
    """Get benefits for a tier level."""
    return LOYALTY_TIERS.get(tier_level, LOYALTY_TIERS["bronze"])


def get_account_with_benefits(
    db: Session,
    user_id: str,
) -> dict:
    """Get loyalty account with tier benefits and stats."""
    account = db.scalar(
        select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id)
    )

    if not account:
        user = db.scalar(select(User).where(User.id == user_id))
        if not user:
            return {}
        account = get_or_create_loyalty_account(db, user)

    tier_benefits = get_loyalty_benefits(account.tier_level)

    # Calculate progress to next tier
    tier_thresholds = [
        LOYALTY_TIERS["bronze"]["min_spend"],
        LOYALTY_TIERS["silver"]["min_spend"],
        LOYALTY_TIERS["gold"]["min_spend"],
    ]

    current_tier_idx = list(LOYALTY_TIERS.keys()).index(account.tier_level)
    if current_tier_idx < len(tier_thresholds) - 1:
        current_threshold = tier_thresholds[current_tier_idx]
        next_threshold = tier_thresholds[current_tier_idx + 1]
        progress = (account.lifetime_spend - current_threshold) / (
            next_threshold - current_threshold
        )
        progress_percent = min(max(progress * 100, 0), 100)
    else:
        progress_percent = 100

    return {
        "account_id": str(account.id),
        "user_id": str(account.user_id),
        "total_points": account.total_points,
        "tier_level": account.tier_level,
        "tier_progress_percent": progress_percent,
        "lifetime_spend": account.lifetime_spend,
        "tier_upgraded_at": account.tier_upgraded_at,
        "benefits": {
            "discount_percent": tier_benefits["discount_percent"],
            "points_multiplier": tier_benefits["points_multiplier"],
            "free_shipping_threshold": tier_benefits["free_shipping_threshold"],
            "birthday_bonus": tier_benefits["birthday_bonus"],
        },
        "points_value_ghs": account.total_points / 100,  # 100 points = 1 GHS
    }


def get_loyalty_transactions(
    db: Session,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Get loyalty transaction history for a user."""
    account = db.scalar(
        select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id)
    )

    if not account:
        return []

    transactions = db.scalars(
        select(LoyaltyTransaction)
        .where(LoyaltyTransaction.account_id == account.id)
        .order_by(LoyaltyTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    return [
        {
            "id": str(tx.id),
            "type": tx.transaction_type,
            "points": tx.points_amount,
            "reason": tx.reason,
            "created_at": tx.created_at,
        }
        for tx in transactions
    ]
