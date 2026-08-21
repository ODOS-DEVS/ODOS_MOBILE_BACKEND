"""Advanced analytics controller."""

from sqlalchemy.orm import Session

from app.models import User
from app.core.auth import require_admin
from app.services.advanced_analytics_service import AdvancedAnalyticsService


def get_customer_metrics(db: Session, current_user: User) -> dict:
    """Get customer metrics."""
    require_admin(current_user)
    return AdvancedAnalyticsService.get_customer_metrics(db)


def get_revenue_metrics(db: Session, current_user: User, days: int = 30) -> dict:
    """Get revenue metrics."""
    require_admin(current_user)
    return AdvancedAnalyticsService.get_revenue_metrics(db, days=days)


def get_product_metrics(db: Session, current_user: User) -> dict:
    """Get product metrics."""
    require_admin(current_user)
    return AdvancedAnalyticsService.get_product_metrics(db)


def get_inventory_metrics(db: Session, current_user: User) -> dict:
    """Get inventory metrics."""
    require_admin(current_user)
    return AdvancedAnalyticsService.get_inventory_metrics(db)


def get_category_performance(db: Session, current_user: User, limit: int = 10) -> dict:
    """Get category performance metrics."""
    require_admin(current_user)
    return {
        "categories": AdvancedAnalyticsService.get_category_performance(db, limit=limit)
    }


def get_vendor_metrics(db: Session, current_user: User) -> dict:
    """Get vendor metrics."""
    require_admin(current_user)
    return AdvancedAnalyticsService.get_vendor_metrics(db)
