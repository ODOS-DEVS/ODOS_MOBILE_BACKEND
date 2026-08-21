"""Customer segmentation service for targeted campaigns."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from enum import Enum
from dataclasses import dataclass

from sqlalchemy import select, func, and_
from sqlalchemy.orm import Session

from app.models import User, Order, UserBehaviorEvent


class CustomerSegment(str, Enum):
    """Customer segment types."""
    NEW = "new"
    ACTIVE = "active"
    AT_RISK = "at_risk"
    DORMANT = "dormant"
    VIP = "vip"
    HIGH_CHURN_RISK = "high_churn_risk"


@dataclass
class CustomerProfile:
    """Customer profile with segmentation data."""
    user_id: str
    email: str
    name: str
    segment: CustomerSegment
    lifetime_spend: float
    total_orders: int
    days_since_last_purchase: int
    average_order_value: float
    purchase_frequency_days: Optional[float]
    last_purchase_date: Optional[datetime]
    first_purchase_date: Optional[datetime]
    engagement_score: float
    churn_risk_score: float


class CustomerSegmentationService:
    """Service for segmenting customers for targeted campaigns."""

    # Segmentation thresholds
    NEW_CUSTOMER_DAYS = 30
    ACTIVE_DAYS_THRESHOLD = 60
    DORMANT_DAYS_THRESHOLD = 120
    VIP_LIFETIME_SPEND = 5000
    AT_RISK_DAYS = 90

    @staticmethod
    def get_customer_profile(
        db: Session,
        user_id: str,
    ) -> Optional[CustomerProfile]:
        """Get customer profile with engagement metrics."""
        user = db.scalar(select(User).where(User.id == user_id))
        if not user:
            return None

        # Get order statistics
        orders = db.scalars(
            select(Order).where(Order.user_id == user_id).order_by(Order.created_at.desc())
        ).all()

        if not orders:
            # New customer with no purchases
            return CustomerProfile(
                user_id=str(user.id),
                email=user.email,
                name=user.full_name or "Customer",
                segment=CustomerSegment.NEW,
                lifetime_spend=0.0,
                total_orders=0,
                days_since_last_purchase=999,
                average_order_value=0.0,
                purchase_frequency_days=None,
                last_purchase_date=None,
                first_purchase_date=None,
                engagement_score=0.5,
                churn_risk_score=0.0,
            )

        total_spend = sum(order.total_amount for order in orders)
        avg_order_value = total_spend / len(orders)
        last_purchase = orders[0].created_at
        first_purchase = orders[-1].created_at

        # Calculate days since last purchase
        days_since_last = (datetime.now(timezone.utc) - last_purchase).days

        # Calculate purchase frequency
        if len(orders) > 1:
            total_days = (orders[0].created_at - orders[-1].created_at).days
            purchase_frequency = total_days / (len(orders) - 1) if len(orders) > 1 else None
        else:
            purchase_frequency = None

        # Determine segment
        segment = CustomerSegmentationService._determine_segment(
            days_since_last,
            total_spend,
            len(orders),
            user.created_at,
        )

        # Calculate engagement score (0-1)
        engagement_score = CustomerSegmentationService._calculate_engagement_score(
            days_since_last,
            len(orders),
            avg_order_value,
        )

        # Calculate churn risk (0-1, higher = more risk)
        churn_risk = CustomerSegmentationService._calculate_churn_risk(
            days_since_last,
            purchase_frequency,
            segment,
        )

        return CustomerProfile(
            user_id=str(user.id),
            email=user.email,
            name=user.full_name or "Customer",
            segment=segment,
            lifetime_spend=round(total_spend, 2),
            total_orders=len(orders),
            days_since_last_purchase=days_since_last,
            average_order_value=round(avg_order_value, 2),
            purchase_frequency_days=round(purchase_frequency, 1) if purchase_frequency else None,
            last_purchase_date=last_purchase,
            first_purchase_date=first_purchase,
            engagement_score=round(engagement_score, 2),
            churn_risk_score=round(churn_risk, 2),
        )

    @staticmethod
    def get_segment_users(
        db: Session,
        segment: CustomerSegment,
        limit: int = 1000,
    ) -> list[CustomerProfile]:
        """Get all users in a specific segment."""
        users = db.scalars(select(User).where(User.status == "active").limit(limit)).all()

        profiles = []
        for user in users:
            profile = CustomerSegmentationService.get_customer_profile(db, str(user.id))
            if profile and profile.segment == segment:
                profiles.append(profile)

        return profiles

    @staticmethod
    def get_churn_risk_users(
        db: Session,
        threshold: float = 0.7,
    ) -> list[CustomerProfile]:
        """Get users at high risk of churning."""
        users = db.scalars(select(User).where(User.status == "active")).all()

        high_risk_profiles = []
        for user in users:
            profile = CustomerSegmentationService.get_customer_profile(db, str(user.id))
            if profile and profile.churn_risk_score >= threshold:
                high_risk_profiles.append(profile)

        return high_risk_profiles

    @staticmethod
    def get_segment_statistics(
        db: Session,
        segment: CustomerSegment,
    ) -> dict:
        """Get statistics for a segment."""
        profiles = CustomerSegmentationService.get_segment_users(db, segment, limit=10000)

        if not profiles:
            return {
                "segment": segment.value,
                "user_count": 0,
                "avg_lifetime_spend": 0.0,
                "avg_order_value": 0.0,
                "avg_orders": 0,
                "avg_engagement_score": 0.0,
            }

        return {
            "segment": segment.value,
            "user_count": len(profiles),
            "avg_lifetime_spend": round(
                sum(p.lifetime_spend for p in profiles) / len(profiles), 2
            ),
            "avg_order_value": round(
                sum(p.average_order_value for p in profiles) / len(profiles), 2
            ),
            "avg_orders": round(
                sum(p.total_orders for p in profiles) / len(profiles), 2
            ),
            "avg_engagement_score": round(
                sum(p.engagement_score for p in profiles) / len(profiles), 2
            ),
            "avg_churn_risk": round(
                sum(p.churn_risk_score for p in profiles) / len(profiles), 2
            ),
        }

    @staticmethod
    def _determine_segment(
        days_since_last_purchase: int,
        lifetime_spend: float,
        order_count: int,
        signup_date: datetime,
    ) -> CustomerSegment:
        """Determine customer segment based on metrics."""
        days_since_signup = (datetime.now(timezone.utc) - signup_date).days

        # VIP check
        if lifetime_spend >= CustomerSegmentationService.VIP_LIFETIME_SPEND:
            return CustomerSegment.VIP

        # New check
        if days_since_signup <= CustomerSegmentationService.NEW_CUSTOMER_DAYS:
            return CustomerSegment.NEW

        # Dormant check
        if days_since_last_purchase >= CustomerSegmentationService.DORMANT_DAYS_THRESHOLD:
            return CustomerSegment.DORMANT

        # At-risk check
        if (
            CustomerSegmentationService.ACTIVE_DAYS_THRESHOLD
            <= days_since_last_purchase
            < CustomerSegmentationService.DORMANT_DAYS_THRESHOLD
        ):
            return CustomerSegment.AT_RISK

        # High churn risk (active but declining)
        if days_since_last_purchase >= CustomerSegmentationService.AT_RISK_DAYS:
            return CustomerSegment.HIGH_CHURN_RISK

        # Default to active
        return CustomerSegment.ACTIVE

    @staticmethod
    def _calculate_engagement_score(
        days_since_last_purchase: int,
        order_count: int,
        avg_order_value: float,
    ) -> float:
        """Calculate engagement score (0-1)."""
        # Recency component (0-1)
        recency_score = max(0, 1 - (days_since_last_purchase / 365))

        # Frequency component (0-1)
        frequency_score = min(1, order_count / 10)

        # Monetary component (0-1)
        monetary_score = min(1, avg_order_value / 500)

        # Weighted average
        engagement = (recency_score * 0.5) + (frequency_score * 0.3) + (monetary_score * 0.2)

        return max(0, min(1, engagement))

    @staticmethod
    def _calculate_churn_risk(
        days_since_last_purchase: int,
        purchase_frequency_days: Optional[float],
        segment: CustomerSegment,
    ) -> float:
        """Calculate churn risk score (0-1, higher = more risk)."""
        # Base risk from days since purchase
        base_risk = min(1, days_since_last_purchase / 180)

        # Segment-based risk modifier
        segment_risk = {
            CustomerSegment.NEW: 0.3,
            CustomerSegment.ACTIVE: 0.1,
            CustomerSegment.AT_RISK: 0.6,
            CustomerSegment.DORMANT: 0.9,
            CustomerSegment.VIP: 0.05,
            CustomerSegment.HIGH_CHURN_RISK: 0.8,
        }.get(segment, 0.5)

        # Frequency-based risk
        if purchase_frequency_days:
            frequency_risk = min(1, days_since_last_purchase / purchase_frequency_days)
        else:
            frequency_risk = 0.5

        # Weighted risk
        churn_risk = (base_risk * 0.4) + (segment_risk * 0.3) + (frequency_risk * 0.3)

        return max(0, min(1, churn_risk))
