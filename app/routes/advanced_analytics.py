"""Advanced analytics routes."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.controllers.advanced_analytics_controller import (
    get_customer_metrics,
    get_revenue_metrics,
    get_product_metrics,
    get_inventory_metrics,
    get_category_performance,
    get_vendor_metrics,
)
from app.models import User

router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"])


@router.get("/customers")
def get_customers_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get customer metrics."""
    return get_customer_metrics(db, current_user)


@router.get("/revenue")
def get_revenue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get revenue metrics."""
    return get_revenue_metrics(db, current_user, days=days)


@router.get("/products")
def get_products_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get product metrics."""
    return get_product_metrics(db, current_user)


@router.get("/inventory")
def get_inventory(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get inventory metrics."""
    return get_inventory_metrics(db, current_user)


@router.get("/categories")
def get_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Get category performance metrics."""
    return get_category_performance(db, current_user, limit=limit)


@router.get("/vendors")
def get_vendors_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get vendor metrics."""
    return get_vendor_metrics(db, current_user)
