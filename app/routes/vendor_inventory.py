"""Vendor inventory management routes."""

from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.controllers.vendor_inventory_controller import (
    list_vendor_inventory,
    get_inventory_stats,
    update_product_stock,
    update_product_price,
    bulk_update_inventory,
    get_low_stock_alerts,
)
from app.models import User


class BulkUpdateRequest(BaseModel):
    """Bulk inventory update request."""
    store_id: str
    updates: list[dict]

router = APIRouter(prefix="/vendor/inventory", tags=["vendor-inventory"])


@router.get("/")
def list_inventory(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    store_id: str = Query(..., description="Store ID"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
):
    """List vendor's inventory with filters."""
    return list_vendor_inventory(
        db,
        current_user,
        store_id,
        skip=skip,
        limit=limit,
        search=search,
        status=status,
    )


@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    store_id: str = Query(..., description="Store ID"),
):
    """Get inventory statistics."""
    return get_inventory_stats(db, current_user, store_id)


@router.patch("/products/{product_id}/stock")
def update_stock(
    product_id: str,
    new_stock: int = Query(..., ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update product stock level."""
    return update_product_stock(db, current_user, product_id, new_stock)


@router.patch("/products/{product_id}/price")
def update_price(
    product_id: str,
    new_price: float = Query(..., ge=0),
    old_price: float | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update product price."""
    return update_product_price(db, current_user, product_id, new_price, old_price)


@router.post("/bulk-update")
def bulk_update(
    request: BulkUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bulk update inventory.

    Each update should be: {\"product_id\": \"...\", \"stock\": 10}
    """
    return bulk_update_inventory(db, current_user, request.store_id, request.updates)


@router.get("/alerts/low-stock")
def get_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    store_id: str = Query(...),
    threshold: int = Query(default=10, ge=0),
):
    """Get low stock alerts."""
    return get_low_stock_alerts(db, current_user, store_id, threshold=threshold)
