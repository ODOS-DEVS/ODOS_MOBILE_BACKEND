"""Vendor inventory controller."""

from sqlalchemy.orm import Session

from app.models import User
from app.core.auth import require_vendor
from app.services.vendor_inventory_service import InventoryService


def list_vendor_inventory(
    db: Session,
    current_user: User,
    store_id: str,
    skip: int = 0,
    limit: int = 50,
    search: str | None = None,
    status: str | None = None,
) -> dict:
    """List vendor's inventory."""
    require_vendor(current_user)

    products, total = InventoryService.get_vendor_inventory(
        db,
        str(current_user.id),
        store_id,
        skip=skip,
        limit=limit,
        search=search,
        status=status,
    )

    return {
        "products": products,
        "total": total,
        "skip": skip,
        "limit": limit,
    }


def get_inventory_stats(
    db: Session,
    current_user: User,
    store_id: str,
) -> dict:
    """Get inventory statistics."""
    require_vendor(current_user)

    return InventoryService.get_inventory_stats(
        db,
        str(current_user.id),
        store_id,
    )


def update_product_stock(
    db: Session,
    current_user: User,
    product_id: str,
    new_stock: int,
) -> dict:
    """Update product stock."""
    require_vendor(current_user)

    success = InventoryService.update_product_stock(
        db,
        str(current_user.id),
        product_id,
        new_stock,
    )

    if success:
        return {
            "success": True,
            "message": f"Updated stock to {new_stock}",
        }
    else:
        return {
            "success": False,
            "message": "Product not found or unauthorized",
        }


def update_product_price(
    db: Session,
    current_user: User,
    product_id: str,
    new_price: float,
    old_price: float | None = None,
) -> dict:
    """Update product price."""
    require_vendor(current_user)

    if new_price < 0:
        return {
            "success": False,
            "message": "Price must be non-negative",
        }

    success = InventoryService.update_product_price(
        db,
        str(current_user.id),
        product_id,
        new_price,
        old_price,
    )

    if success:
        return {
            "success": True,
            "message": f"Updated price to {new_price}",
        }
    else:
        return {
            "success": False,
            "message": "Product not found or unauthorized",
        }


def bulk_update_inventory(
    db: Session,
    current_user: User,
    store_id: str,
    updates: list[dict],
) -> dict:
    """Bulk update inventory."""
    require_vendor(current_user)

    successful, failed = InventoryService.bulk_update_stock(
        db,
        str(current_user.id),
        updates,
    )

    return {
        "successful": successful,
        "failed": failed,
        "total": len(updates),
        "message": f"Updated {successful} products, {failed} failed",
    }


def get_low_stock_alerts(
    db: Session,
    current_user: User,
    store_id: str,
    threshold: int = 10,
) -> dict:
    """Get low stock alerts."""
    require_vendor(current_user)

    products = InventoryService.get_low_stock_products(
        db,
        str(current_user.id),
        store_id,
        threshold=threshold,
    )

    return {
        "alerts": products,
        "count": len(products),
        "threshold": threshold,
    }
