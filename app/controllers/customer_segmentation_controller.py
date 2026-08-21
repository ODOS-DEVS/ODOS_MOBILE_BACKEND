"""Customer segmentation controller."""

from sqlalchemy.orm import Session

from app.models import User
from app.core.auth import require_admin
from app.services.customer_segmentation_service import (
    CustomerSegmentationService,
    CustomerSegment,
)


def get_customer_profile(
    db: Session,
    current_user: User,
    user_id: str,
) -> dict:
    """Get customer profile with segmentation data."""
    require_admin(current_user)

    profile = CustomerSegmentationService.get_customer_profile(db, user_id)

    if not profile:
        return {
            "success": False,
            "message": "User not found",
        }

    return {
        "success": True,
        "profile": {
            "user_id": profile.user_id,
            "email": profile.email,
            "name": profile.name,
            "segment": profile.segment.value,
            "lifetime_spend": profile.lifetime_spend,
            "total_orders": profile.total_orders,
            "days_since_last_purchase": profile.days_since_last_purchase,
            "average_order_value": profile.average_order_value,
            "purchase_frequency_days": profile.purchase_frequency_days,
            "engagement_score": profile.engagement_score,
            "churn_risk_score": profile.churn_risk_score,
        },
    }


def get_segment_members(
    db: Session,
    current_user: User,
    segment: str,
    limit: int = 100,
) -> dict:
    """Get all members of a customer segment."""
    require_admin(current_user)

    try:
        segment_enum = CustomerSegment[segment.upper()]
    except KeyError:
        return {
            "success": False,
            "message": f"Invalid segment: {segment}",
        }

    profiles = CustomerSegmentationService.get_segment_users(db, segment_enum, limit=limit)

    return {
        "success": True,
        "segment": segment,
        "count": len(profiles),
        "members": [
            {
                "user_id": p.user_id,
                "email": p.email,
                "name": p.name,
                "lifetime_spend": p.lifetime_spend,
                "total_orders": p.total_orders,
                "engagement_score": p.engagement_score,
            }
            for p in profiles
        ],
    }


def get_segment_statistics(
    db: Session,
    current_user: User,
    segment: str,
) -> dict:
    """Get statistics for a segment."""
    require_admin(current_user)

    try:
        segment_enum = CustomerSegment[segment.upper()]
    except KeyError:
        return {
            "success": False,
            "message": f"Invalid segment: {segment}",
        }

    stats = CustomerSegmentationService.get_segment_statistics(db, segment_enum)

    return {
        "success": True,
        "statistics": stats,
    }


def get_all_segments_overview(
    db: Session,
    current_user: User,
) -> dict:
    """Get overview statistics for all segments."""
    require_admin(current_user)

    all_segments = [
        CustomerSegment.NEW,
        CustomerSegment.ACTIVE,
        CustomerSegment.AT_RISK,
        CustomerSegment.DORMANT,
        CustomerSegment.VIP,
        CustomerSegment.HIGH_CHURN_RISK,
    ]

    segments_overview = []
    for segment in all_segments:
        stats = CustomerSegmentationService.get_segment_statistics(db, segment)
        segments_overview.append(stats)

    total_users = sum(seg["user_count"] for seg in segments_overview)
    total_spend = sum(seg["avg_lifetime_spend"] * seg["user_count"] for seg in segments_overview)

    return {
        "success": True,
        "total_users": total_users,
        "total_lifetime_spend": round(total_spend, 2),
        "segments": segments_overview,
    }


def get_churn_risk_users(
    db: Session,
    current_user: User,
    threshold: float = 0.7,
    limit: int = 100,
) -> dict:
    """Get users at high risk of churning."""
    require_admin(current_user)

    if not (0 <= threshold <= 1):
        return {
            "success": False,
            "message": "Threshold must be between 0 and 1",
        }

    profiles = CustomerSegmentationService.get_churn_risk_users(db, threshold=threshold)

    return {
        "success": True,
        "threshold": threshold,
        "count": len(profiles),
        "users": [
            {
                "user_id": p.user_id,
                "email": p.email,
                "name": p.name,
                "churn_risk_score": p.churn_risk_score,
                "days_since_last_purchase": p.days_since_last_purchase,
                "lifetime_spend": p.lifetime_spend,
                "segment": p.segment.value,
            }
            for p in profiles[:limit]
        ],
    }


def export_segment_for_campaign(
    db: Session,
    current_user: User,
    segment: str,
) -> dict:
    """Export segment data for email campaign."""
    require_admin(current_user)

    try:
        segment_enum = CustomerSegment[segment.upper()]
    except KeyError:
        return {
            "success": False,
            "message": f"Invalid segment: {segment}",
        }

    profiles = CustomerSegmentationService.get_segment_users(db, segment_enum, limit=10000)

    # Format for email campaign import
    emails = [p.email for p in profiles]
    csv_data = "email,name,segment,lifetime_spend,churn_risk\n"
    for p in profiles:
        csv_data += f"{p.email},{p.name},{segment},{p.lifetime_spend},{p.churn_risk_score}\n"

    return {
        "success": True,
        "segment": segment,
        "count": len(profiles),
        "emails": emails,
        "csv": csv_data,
    }
