"""Vendor inventory management service."""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import and_, or_, select, func
from sqlalchemy.orm import Session

from app.models import Product, Store, User, UserBehaviorEvent


class InventoryService:
    """Service for vendor inventory operations."""

    @staticmethod
    def get_vendor_inventory(
        db: Session,
        vendor_id: str,
        store_id: str,
        skip: int = 0,
        limit: int = 50,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> tuple[list[dict], int]:
        """Get vendor's products with inventory info."""
        # Verify store ownership
        store = db.scalar(
            select(Store).where(
                Store.id == store_id,
                Store.vendor_id == vendor_id,
            )
        )

        if not store:
            return [], 0

        query = select(Product).where(Product.store_id == store_id)

        # Apply filters
        if search:
            search_lower = f"%{search.lower()}%"
            query = query.where(
                or_(
                    Product.title.ilike(search_lower),
                    Product.description.ilike(search_lower),
                    Product.sku.ilike(search_lower) if hasattr(Product, 'sku') else False,
                )
            )

        if status:
            query = query.where(Product.status == status)

        # Get total count
        total = db.scalar(select(func.count(Product.id)).select_from(query.alias()))

        # Paginate
        products = db.scalars(
            query.offset(skip).limit(limit)
        ).all()

        # Build response with inventory info
        result = []
        for product in products:
            # Get view count for popularity
            view_count = db.scalar(
                select(func.count(UserBehaviorEvent.id)).where(
                    UserBehaviorEvent.product_id == product.id,
                    UserBehaviorEvent.event_type.in_(["product_view", "product_click"]),
                )
            ) or 0

            # Get sales count
            sales_count = db.scalar(
                select(func.count(UserBehaviorEvent.id)).where(
                    UserBehaviorEvent.product_id == product.id,
                    UserBehaviorEvent.event_type == "purchase",
                )
            ) or 0

            result.append({
                "id": product.id,
                "title": product.title,
                "sku": getattr(product, 'sku', None),
                "price": product.price,
                "old_price": product.old_price,
                "stock": product.stock,
                "status": product.status,
                "rating": product.rating,
                "category": product.category,
                "views": view_count,
                "sales": sales_count,
                "created_at": product.created_at,
                "updated_at": product.updated_at,
                "low_stock": product.stock <= 10,
            })

        return result, total

    @staticmethod
    def update_product_stock(
        db: Session,
        vendor_id: str,
        product_id: str,
        new_stock: int,
    ) -> bool:
        """Update product stock level."""
        product = db.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.store_id == (
                    select(Store.id).where(Store.vendor_id == vendor_id)
                ),
            )
        )

        if not product:
            return False

        product.stock = max(0, new_stock)
        product.updated_at = datetime.now(timezone.utc)
        db.commit()
        return True

    @staticmethod
    def update_product_price(
        db: Session,
        vendor_id: str,
        product_id: str,
        new_price: float,
        old_price: Optional[float] = None,
    ) -> bool:
        """Update product pricing."""
        product = db.scalar(
            select(Product).where(
                Product.id == product_id,
                Product.store_id == (
                    select(Store.id).where(Store.vendor_id == vendor_id)
                ),
            )
        )

        if not product:
            return False

        product.price = max(0, new_price)
        if old_price is not None:
            product.old_price = max(0, old_price)
        product.updated_at = datetime.now(timezone.utc)
        db.commit()
        return True

    @staticmethod
    def bulk_update_stock(
        db: Session,
        vendor_id: str,
        updates: list[dict],  # [{product_id: str, stock: int}, ...]
    ) -> tuple[int, int]:  # (successful, failed)
        """Bulk update stock levels."""
        successful = 0
        failed = 0

        for update in updates:
            product_id = update.get("product_id")
            stock = update.get("stock")

            if not product_id or stock is None:
                failed += 1
                continue

            if InventoryService.update_product_stock(db, vendor_id, product_id, stock):
                successful += 1
            else:
                failed += 1

        return successful, failed

    @staticmethod
    def get_low_stock_products(
        db: Session,
        vendor_id: str,
        store_id: str,
        threshold: int = 10,
    ) -> list[dict]:
        """Get products below stock threshold."""
        store = db.scalar(
            select(Store).where(
                Store.id == store_id,
                Store.vendor_id == vendor_id,
            )
        )

        if not store:
            return []

        products = db.scalars(
            select(Product).where(
                Product.store_id == store_id,
                Product.stock <= threshold,
                Product.status == "active",
            ).order_by(Product.stock.asc())
        ).all()

        return [
            {
                "id": product.id,
                "title": product.title,
                "stock": product.stock,
                "threshold": threshold,
                "status": "critical" if product.stock == 0 else "low",
            }
            for product in products
        ]

    @staticmethod
    def get_inventory_stats(
        db: Session,
        vendor_id: str,
        store_id: str,
    ) -> dict:
        """Get inventory overview statistics."""
        store = db.scalar(
            select(Store).where(
                Store.id == store_id,
                Store.vendor_id == vendor_id,
            )
        )

        if not store:
            return {}

        total_products = db.scalar(
            select(func.count(Product.id)).where(Product.store_id == store_id)
        ) or 0

        active_products = db.scalar(
            select(func.count(Product.id)).where(
                Product.store_id == store_id,
                Product.status == "active",
            )
        ) or 0

        out_of_stock = db.scalar(
            select(func.count(Product.id)).where(
                Product.store_id == store_id,
                Product.stock == 0,
            )
        ) or 0

        low_stock = db.scalar(
            select(func.count(Product.id)).where(
                Product.store_id == store_id,
                Product.stock <= 10,
                Product.stock > 0,
            )
        ) or 0

        total_stock_value = db.scalar(
            select(func.coalesce(func.sum(Product.price * Product.stock), 0)).where(
                Product.store_id == store_id,
            )
        ) or 0

        return {
            "total_products": total_products,
            "active_products": active_products,
            "out_of_stock": out_of_stock,
            "low_stock": low_stock,
            "total_stock_value": round(float(total_stock_value), 2),
            "avg_price": (
                db.scalar(
                    select(func.avg(Product.price)).where(
                        Product.store_id == store_id,
                        Product.price > 0,
                    )
                ) or 0
            ),
        }
