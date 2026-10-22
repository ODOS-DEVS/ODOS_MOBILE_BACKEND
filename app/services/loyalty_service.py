"""Loyalty rewards service for managing points and tiers."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LoyaltyAccount, LoyaltyTransaction, Order, User

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

POINTS_PER_GHS = 1.0  # 1 point per 1 GHS of goods

#: Points needed to redeem one cedi. With POINTS_PER_GHS above, a bronze
#: shopper earns 1% back and a gold shopper 1.5%. This lived as a bare `/ 100`
#: in two different files; naming it keeps the earn and burn sides of the
#: economy in one place, where a change to one forces a look at the other.
POINTS_PER_GHS_REDEEMED = 100

#: Redemption must be a whole number of cedis. Allowing 150 points would mean
#: either paying out GHS 1.50 (fine) or silently keeping the remainder (not
#: fine); requiring a clean multiple removes the question.
MIN_REDEEMABLE_POINTS = POINTS_PER_GHS_REDEEMED


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
    if lifetime_spend >= LOYALTY_TIERS["silver"]["min_spend"]:
        return "silver"
    return "bronze"


def _order_goods_subtotal(order: Order) -> float:
    """What the customer paid for goods, excluding delivery.

    Points used to accrue on `order.total_amount`, which bundles the shipping
    fee — so a shopper earned loyalty on the rider's wage. Delivery is a
    pass-through cost that ODOS does not profit from (see
    `vendor_allocation_map`, which likewise never commissions it), so it is
    not a base to pay rewards on either.
    """
    subtotal = float(order.subtotal_amount or 0)
    if subtotal > 0:
        return max(0.0, subtotal - float(order.discount_amount or 0))

    # Older orders predate subtotal_amount being populated; fall back to the
    # line items rather than paying out on a zero.
    line_total = sum(float(item.line_total or 0) for item in (order.items or []))
    return max(0.0, line_total - float(order.discount_amount or 0))


def has_earned_points_for_order(db: Session, order_id) -> bool:
    return (
        db.scalar(
            select(LoyaltyTransaction.id).where(
                LoyaltyTransaction.order_id == order_id,
                LoyaltyTransaction.transaction_type == "earn",
            )
        )
        is not None
    )


def award_order_loyalty_points(db: Session, order: Order) -> int:
    """Award points once, when the whole order has been delivered.

    Called from `_complete_package`, the single chokepoint every completion
    path funnels through — customer confirmation, admin override and auto
    release all land here, so points no longer depend on *how* an order
    finished.

    Two guards make that safe to call repeatedly:

      * the order must have no outstanding packages, so a multi-shop order
        pays out once on the last bag rather than once per shop;
      * an existing "earn" row for the order short-circuits, so a retried
        request or a replayed job cannot pay twice.

    Returns the points awarded, or 0 when nothing was due. Deliberately does
    not commit: it runs inside the caller's transaction so points and the
    delivery state that earned them land together or not at all.
    """
    user_id = order.user_id
    if not user_id:
        return 0

    outstanding = [
        package
        for package in (order.packages or [])
        if package.vendor_status != "cancelled" and package.delivery_status != "delivered"
    ]
    if outstanding:
        return 0

    if has_earned_points_for_order(db, order.id):
        return 0

    goods = _order_goods_subtotal(order)
    if goods <= 0:
        return 0

    account = db.scalar(select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id))
    if not account:
        user = db.scalar(select(User).where(User.id == user_id))
        if not user:
            return 0
        account = get_or_create_loyalty_account(db, user)

    base_points = int(goods * POINTS_PER_GHS)
    tier_multiplier = LOYALTY_TIERS[account.tier_level]["points_multiplier"]
    points_earned = int(base_points * tier_multiplier)
    if points_earned <= 0:
        return 0

    old_tier = account.tier_level
    account.total_points += points_earned
    account.lifetime_spend += goods

    new_tier = calculate_tier(account.lifetime_spend)
    if new_tier != old_tier:
        account.tier_level = new_tier
        account.tier_upgraded_at = datetime.now(UTC)

    db.add(
        LoyaltyTransaction(
            account_id=account.id,
            transaction_type="earn",
            points_amount=points_earned,
            reason=f"Order #{order.order_number}",
            order_id=order.id,
            # `metadata=` here silently vanished: SQLAlchemy takes it as an
            # unmapped instance attribute and the column is `metadata_json`,
            # so every earn row was written with an empty audit trail.
            metadata_json={
                "goods_subtotal": round(goods, 2),
                "base_points": base_points,
                "tier_multiplier": tier_multiplier,
                "tier_level": old_tier,
                "tier_upgraded": new_tier != old_tier,
            },
        )
    )

    return points_earned


def reverse_order_loyalty_points(db: Session, order: Order, refund_amount: float) -> int:
    """Claw back points when money goes back to the customer.

    Without this a shopper could buy, collect points, return the goods for a
    full refund and keep the rewards — points are redeemable for cash, so that
    is a withdrawal path, not a generosity.

    Reversal is proportional to the refund and never pushes a balance below
    zero: the points may already have been spent, and a negative balance would
    silently swallow future earnings rather than surfacing a debt anyone can
    act on.
    """
    if refund_amount <= 0 or not order.user_id:
        return 0

    earn_row = db.scalar(
        select(LoyaltyTransaction).where(
            LoyaltyTransaction.order_id == order.id,
            LoyaltyTransaction.transaction_type == "earn",
        )
    )
    if not earn_row:
        return 0

    goods = _order_goods_subtotal(order)
    if goods <= 0:
        return 0

    share = min(1.0, float(refund_amount) / goods)
    points_to_reverse = int(round(earn_row.points_amount * share))
    if points_to_reverse <= 0:
        return 0

    account = db.get(LoyaltyAccount, earn_row.account_id)
    if not account:
        return 0

    points_to_reverse = min(points_to_reverse, account.total_points)
    if points_to_reverse <= 0:
        return 0

    account.total_points -= points_to_reverse
    account.lifetime_spend = max(0.0, account.lifetime_spend - float(refund_amount))

    db.add(
        LoyaltyTransaction(
            account_id=account.id,
            transaction_type="reverse",
            points_amount=-points_to_reverse,
            reason=f"Refund on order #{order.order_number}",
            order_id=order.id,
            metadata_json={
                "refund_amount": round(float(refund_amount), 2),
                "refunded_share": round(share, 4),
            },
        )
    )

    return points_to_reverse


def redeem_points_to_wallet(
    db: Session,
    user_id: str,
    points_to_redeem: int,
) -> dict:
    """Turn points into spendable money, atomically.

    The previous `redeem_points` decremented the balance, wrote a ledger row
    and returned True — and that was all it did. Nothing was credited and
    nothing was discounted: the API answered with a `discount_amount_ghs` the
    checkout had no knowledge of and no code anywhere consumed. A customer who
    redeemed 500 points lost them and received a JSON field. This is the fix.

    Points now land in the ODOS customer wallet, which is an existing, tested
    balance the shopper can already spend. That is deliberately not a new
    bespoke discount path: inventing one would mean a second money rail to
    keep correct, while the wallet already handles crediting, auditing and
    spending.

    Both sides happen in one transaction. A burn that commits without its
    credit is money destroyed, so the commit covers the pair or neither.
    """
    # Imported here rather than at module scope: the wallet helpers live in a
    # controller that imports services, and a top-level import would close the
    # cycle.
    from app.controllers.customer_wallet_controller import (
        get_or_create_customer_wallet_for_update,
    )
    from app.models import CustomerWalletTransaction
    from app.services.finance_math import round_money

    if points_to_redeem < MIN_REDEEMABLE_POINTS:
        return {
            "success": False,
            "message": f"Redeem at least {MIN_REDEEMABLE_POINTS} points (GHS 1).",
        }

    if points_to_redeem % POINTS_PER_GHS_REDEEMED != 0:
        return {
            "success": False,
            "message": f"Redeem in multiples of {POINTS_PER_GHS_REDEEMED} points.",
        }

    account = db.scalar(select(LoyaltyAccount).where(LoyaltyAccount.user_id == user_id))
    if not account or account.total_points < points_to_redeem:
        return {"success": False, "message": "Insufficient loyalty points."}

    cash_value = round_money(points_to_redeem / POINTS_PER_GHS_REDEEMED)
    if cash_value <= 0:
        return {"success": False, "message": "That many points is worth nothing yet."}

    account.total_points -= points_to_redeem

    wallet = get_or_create_customer_wallet_for_update(db, user_id)
    wallet.available_balance = round_money(wallet.available_balance + cash_value)

    db.add(
        LoyaltyTransaction(
            account_id=account.id,
            transaction_type="redeem",
            points_amount=-points_to_redeem,
            reason=f"Redeemed for GHS {cash_value:.2f} wallet credit",
            metadata_json={"cash_value": cash_value},
        )
    )
    db.add(
        CustomerWalletTransaction(
            wallet_id=wallet.id,
            user_id=user_id,
            kind="credit_loyalty_redemption",
            title=f"{points_to_redeem} loyalty points redeemed",
            amount=cash_value,
            balance_after=wallet.available_balance,
            metadata_json={"points_redeemed": points_to_redeem},
        )
    )

    db.commit()
    db.refresh(account)

    return {
        "success": True,
        "message": f"GHS {cash_value:.2f} added to your ODOS wallet.",
        "points_redeemed": points_to_redeem,
        "cash_value_ghs": cash_value,
        "wallet_balance": wallet.available_balance,
    }


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
        "points_value_ghs": account.total_points / POINTS_PER_GHS_REDEEMED,
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
