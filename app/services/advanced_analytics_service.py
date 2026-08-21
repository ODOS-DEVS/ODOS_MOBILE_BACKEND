"""Advanced analytics service for dashboard metrics."""

from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, and_, desc
from sqlalchemy.orm import Session

from app.models import User, Order, Product, Store, Review, UserBehaviorEvent


class AdvancedAnalyticsService:
    """Service for computing advanced analytics metrics."""

    @staticmethod
    def get_customer_metrics(db: Session) -> dict:
        """Get customer metrics."""
        total_customers = db.scalar(
            select(func.count(User.id)).where(User.role == "customer")
        ) or 0

        # New customers today
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        new_today = db.scalar(
            select(func.count(User.id)).where(
                User.role == "customer",
                User.created_at >= today,
            )
        ) or 0

        # Active customers (purchased in last 30 days)
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        active_customers = db.scalar(
            select(func.count(func.distinct(Order.user_id))).where(
                Order.created_at >= thirty_days_ago
            )
        ) or 0

        # Average lifetime value
        avg_ltv = db.scalar(
            select(func.avg(func.coalesce(
                select(func.sum(Order.total_amount))
                .where(Order.user_id == User.id)
                .correlate(User)
                .scalar_subquery(),
                0
            )))
            .select_from(User)
            .where(User.role == "customer")
        ) or 0.0

        # Retention rate (customers who ordered last month and month before)
        prev_month_start = datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)
        prev_month_start = prev_month_start.replace(day=1)
        prev_month_end = datetime.now(timezone.utc).replace(day=1) - timedelta(seconds=1)

        repeat_customers = db.scalar(
            select(func.count(func.distinct(Order.user_id))).where(
                Order.created_at >= prev_month_start,
                Order.created_at <= prev_month_end,
            )
        ) or 0

        retention_rate = (repeat_customers / active_customers) if active_customers > 0 else 0

        return {
            "total_customers": total_customers,
            "new_customers_today": new_today,
            "active_customers": active_customers,
            "retention_rate": min(1.0, retention_rate),
            "average_lifetime_value": round(avg_ltv, 2),
        }

    @staticmethod
    def get_revenue_metrics(db: Session, days: int = 30) -> dict:
        """Get revenue metrics."""
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        # Total revenue (all time)
        total_revenue = db.scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0))
            .where(Order.status.in_(["completed", "delivered"]))
        ) or 0.0

        # Revenue today
        revenue_today = db.scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0))
            .where(
                Order.status.in_(["completed", "delivered"]),
                Order.created_at >= today_start,
            )
        ) or 0.0

        # Revenue last 7 days
        seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
        revenue_7d = db.scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0))
            .where(
                Order.status.in_(["completed", "delivered"]),
                Order.created_at >= seven_days_ago,
            )
        ) or 0.0

        # Revenue last 30 days
        revenue_30d = db.scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0))
            .where(
                Order.status.in_(["completed", "delivered"]),
                Order.created_at >= cutoff_date,
            )
        ) or 0.0

        # Average order value
        avg_order_value = db.scalar(
            select(func.avg(Order.total_amount))
            .where(Order.status.in_(["completed", "delivered"]))
        ) or 0.0

        # Orders today
        orders_today = db.scalar(
            select(func.count(Order.id))
            .where(
                Order.status.in_(["completed", "delivered"]),
                Order.created_at >= today_start,
            )
        ) or 0

        return {
            "total_revenue": round(total_revenue, 2),
            "revenue_today": round(revenue_today, 2),
            "revenue_7d": round(revenue_7d, 2),
            "revenue_30d": round(revenue_30d, 2),
            "avg_order_value": round(avg_order_value, 2),
            "orders_today": orders_today,
        }

    @staticmethod
    def get_product_metrics(db: Session) -> dict:
        """Get product metrics."""
        total_products = db.scalar(
            select(func.count(Product.id)).where(Product.status == "active")
        ) or 0

        low_stock = db.scalar(
            select(func.count(Product.id)).where(
                Product.stock <= 10,
                Product.stock > 0,
                Product.status == "active",
            )
        ) or 0

        out_of_stock = db.scalar(
            select(func.count(Product.id)).where(
                Product.stock == 0,
                Product.status == "active",
            )
        ) or 0

        avg_rating = db.scalar(
            select(func.avg(Product.rating))
            .where(Product.status == "active", Product.rating > 0)
        ) or 0.0

        # Top products by sales
        top_products = db.execute(
            select(
                Product.id,
                Product.title,
                func.count(UserBehaviorEvent.id).label("sales"),
                func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            )
            .select_from(Product)
            .outerjoin(
                UserBehaviorEvent,
                and_(
                    UserBehaviorEvent.product_id == Product.id,
                    UserBehaviorEvent.event_type == "purchase",
                ),
            )
            .outerjoin(Order, Order.id == UserBehaviorEvent.order_id)
            .where(Product.status == "active")
            .group_by(Product.id, Product.title)
            .order_by(desc("sales"))
            .limit(5)
        ).all()

        return {
            "total_products": total_products,
            "low_stock_count": low_stock,
            "out_of_stock_count": out_of_stock,
            "avg_rating": round(avg_rating, 2),
            "top_products": [
                {
                    "id": str(p[0]),
                    "title": p[1],
                    "sales": p[2],
                    "revenue": round(p[3], 2) if p[3] else 0.0,
                }
                for p in top_products
            ],
        }

    @staticmethod
    def get_inventory_metrics(db: Session) -> dict:
        """Get inventory metrics."""
        total_value = db.scalar(
            select(func.coalesce(func.sum(Product.price * Product.stock), 0))
            .where(Product.status == "active")
        ) or 0.0

        total_units = db.scalar(
            select(func.coalesce(func.sum(Product.stock), 0))
            .where(Product.status == "active")
        ) or 0

        # Stock turnover rate (simplified: 30-day sales / avg inventory)
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        units_sold = db.scalar(
            select(func.count(UserBehaviorEvent.id))
            .where(
                UserBehaviorEvent.event_type == "purchase",
                UserBehaviorEvent.created_at >= thirty_days_ago,
            )
        ) or 1

        avg_inventory = total_units / 2 if total_units > 0 else 1
        stock_turnover = units_sold / avg_inventory if avg_inventory > 0 else 0

        # Excess inventory (low-demand high-stock items)
        excess_value = db.scalar(
            select(func.coalesce(func.sum(Product.price * Product.stock), 0))
            .where(
                Product.stock > 50,
                Product.rating < 3.0,
                Product.status == "active",
            )
        ) or 0.0

        optimal_stock = db.scalar(
            select(func.count(Product.id))
            .where(
                Product.stock > 10,
                Product.stock <= 50,
                Product.status == "active",
            )
        ) or 0

        return {
            "total_value": round(total_value, 2),
            "total_units": total_units,
            "stock_turnover_rate": round(stock_turnover, 2),
            "excess_inventory_value": round(excess_value, 2),
            "optimal_stock_count": optimal_stock,
        }

    @staticmethod
    def get_category_performance(db: Session, limit: int = 10) -> list[dict]:
        """Get category performance metrics."""
        results = db.execute(
            select(
                Product.category,
                func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
                func.count(func.distinct(Order.id)).label("orders"),
                func.avg(Product.rating).label("avg_rating"),
            )
            .select_from(Product)
            .outerjoin(
                Order,
                Order.id == (
                    select(UserBehaviorEvent.order_id)
                    .where(UserBehaviorEvent.product_id == Product.id)
                    .correlate(Product)
                    .scalar_subquery()
                ),
            )
            .where(Product.status == "active")
            .group_by(Product.category)
            .order_by(desc("revenue"))
            .limit(limit)
        ).all()

        categories = []
        for row in results:
            categories.append({
                "category": row[0],
                "revenue": round(row[1], 2),
                "orders": row[2],
                "growth_rate": 0.0,  # Would need historical data
                "avg_rating": round(row[3], 2) if row[3] else 0.0,
            })

        return categories

    @staticmethod
    def get_vendor_metrics(db: Session) -> dict:
        """Get vendor metrics."""
        total_vendors = db.scalar(
            select(func.count(User.id)).where(User.role == "vendor")
        ) or 0

        active_vendors = db.scalar(
            select(func.count(func.distinct(Store.vendor_id)))
            .where(Store.is_active == True)
        ) or 0

        # Top vendor by revenue
        top_vendor = db.execute(
            select(
                Store.name,
                func.coalesce(func.sum(Order.total_amount), 0).label("revenue"),
            )
            .select_from(Store)
            .outerjoin(
                Product,
                Product.store_id == Store.id,
            )
            .outerjoin(Order, Order.id == (
                select(UserBehaviorEvent.order_id)
                .where(UserBehaviorEvent.product_id == Product.id)
                .correlate(Product)
                .scalar_subquery()
            ))
            .group_by(Store.id, Store.name)
            .order_by(desc("revenue"))
            .limit(1)
        ).first()

        avg_rating = db.scalar(
            select(func.avg(Store.rating))
            .where(Store.is_active == True)
        ) or 0.0

        avg_products = db.scalar(
            select(func.avg(func.count(Product.id)))
            .select_from(Store)
            .join(Product)
            .group_by(Store.id)
        ) or 0.0

        return {
            "total_vendors": total_vendors,
            "active_vendors": active_vendors,
            "top_vendor": {
                "name": top_vendor[0] if top_vendor else "N/A",
                "revenue": round(top_vendor[1], 2) if top_vendor else 0.0,
            },
            "avg_vendor_rating": round(avg_rating, 2),
            "avg_vendor_products": round(avg_products, 0),
        }
