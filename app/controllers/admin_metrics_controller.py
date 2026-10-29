"""Admin dashboard metrics and KPI endpoints."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.admin_permissions import require_admin
from app.models import Order, OrderItem, Store, User, VendorStatus

#: Order states that represent money actually earned — see
#: advanced_analytics_service.SALES_ORDER_STATUSES, kept identical so the
#: dashboard and the analytics pages cannot disagree about what a sale is.
SALES_ORDER_STATUSES = ("confirmed", "processing", "ready", "out_for_delivery", "delivered")


def get_sales_chart_timeseries(
    db: Session,
    current_user: User,
    days: int = 7,
) -> dict:
    """Get sales timeseries data for chart visualization."""
    require_admin(current_user)

    if days not in [7, 30, 90]:
        days = 7

    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)

    # One expression object, reused in all three clauses.
    #
    # Building `func.date_trunc(...)` separately per clause produced three
    # distinct expressions, and Postgres rejected the statement with
    # "orders.created_at must appear in the GROUP BY clause" because what was
    # grouped was not recognised as what was selected. Binding it once makes
    # select, group and order provably the same expression.
    day = func.date_trunc("day", Order.created_at)

    # Counted on vendor_status, matching SALES_ORDER_STATUSES used by the
    # vendor dashboard, so the same orders produce the same revenue on both
    # sides. The previous filter on `status in (paid, delivered)` answered a
    # different question and could not agree with the seller's own figures.
    query = select(
        day.label("date"),
        func.count(Order.id).label("order_count"),
        func.coalesce(func.sum(Order.total_amount), 0.0).label("revenue"),
    ).where(
        Order.created_at >= start_date,
        Order.vendor_status.in_(SALES_ORDER_STATUSES),
    ).group_by(day).order_by(day)

    results = db.execute(query).all()

    data = []
    for result in results:
        data.append({
            "date": result.date.isoformat() if result.date else None,
            "orders": result.order_count,
            "revenue": round(float(result.revenue), 2),
        })

    total_revenue = sum(item["revenue"] for item in data)
    total_orders = sum(item["orders"] for item in data)
    avg_daily_revenue = total_revenue / max(days, 1) if data else 0

    return {
        "period_days": days,
        "data": data,
        "summary": {
            "total_revenue": round(total_revenue, 2),
            "total_orders": total_orders,
            "avg_daily_revenue": round(avg_daily_revenue, 2),
            "avg_order_value": round(total_revenue / max(total_orders, 1), 2),
        },
    }


def get_top_vendors(
    db: Session,
    current_user: User,
    limit: int = 10,
    days: int = 30,
) -> list[dict]:
    """Get top vendors by GMV (Gross Merchandise Value) in period."""
    require_admin(current_user)

    if limit > 50:
        limit = 50
    if limit < 1:
        limit = 10

    start_date = datetime.now(UTC) - timedelta(days=days)

    # Three corrections, each of which alone raised an error:
    #
    #   * `Order.store_id` does not exist. An order can carry items from
    #     several shops, so the shop lives on OrderItem, and that is the join
    #     that makes a multi-vendor order count once per shop rather than
    #     attributing the whole basket to one of them.
    #   * `OrderItem.price` does not exist either; the column is `unit_price`,
    #     and `line_total` already holds unit_price * quantity.
    #   * counted on vendor_status, for the same agreement with the seller's
    #     own figures as the chart above.
    gmv = func.coalesce(func.sum(OrderItem.line_total), 0.0)
    query = select(
        Store.id,
        Store.title,
        Store.image_url,
        func.count(func.distinct(Order.id)).label("order_count"),
        gmv.label("gmv"),
        func.coalesce(func.avg(Order.total_amount), 0.0).label("avg_order_value"),
    ).join(
        OrderItem, OrderItem.store_id == Store.id,
    ).join(
        Order, Order.id == OrderItem.order_id,
    ).where(
        Order.created_at >= start_date,
        Order.vendor_status.in_(SALES_ORDER_STATUSES),
        Store.status == "active",
    ).group_by(
        Store.id,
        Store.title,
        Store.image_url,
    ).order_by(gmv.desc()).limit(limit)

    results = db.execute(query).all()

    data = []
    for idx, result in enumerate(results, 1):
        data.append({
            "rank": idx,
            "store_id": result.id,
            "store_name": result.title,
            "store_image": result.image_url,
            "orders": result.order_count,
            "gmv": round(float(result.gmv), 2),
            "avg_order_value": round(float(result.avg_order_value), 2),
        })

    return data


def get_kpi_metrics(
    db: Session,
    current_user: User,
) -> dict:
    """Get key performance indicator metrics."""
    require_admin(current_user)

    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)
    last_month_end = this_month_start - timedelta(seconds=1)

    # Current metrics
    today_orders = db.scalar(
        select(func.count(Order.id)).where(Order.created_at >= today_start)
    ) or 0
    today_revenue = db.scalar(
        select(func.coalesce(func.sum(Order.total_amount), 0.0)).where(
            Order.created_at >= today_start,
            Order.status.in_(["paid", "delivered"]),
        )
    ) or 0.0

    month_orders = db.scalar(
        select(func.count(Order.id)).where(Order.created_at >= this_month_start)
    ) or 0
    month_revenue = db.scalar(
        select(func.coalesce(func.sum(Order.total_amount), 0.0)).where(
            Order.created_at >= this_month_start,
            Order.status.in_(["paid", "delivered"]),
        )
    ) or 0.0

    # Last month for comparison
    last_month_orders = db.scalar(
        select(func.count(Order.id)).where(
            Order.created_at >= last_month_start,
            Order.created_at <= last_month_end,
        )
    ) or 0
    last_month_revenue = db.scalar(
        select(func.coalesce(func.sum(Order.total_amount), 0.0)).where(
            Order.created_at >= last_month_start,
            Order.created_at <= last_month_end,
            Order.status.in_(["paid", "delivered"]),
        )
    ) or 0.0

    # Growth percentages
    month_order_growth = (
        ((month_orders - last_month_orders) / max(last_month_orders, 1)) * 100
        if last_month_orders > 0
        else 0
    )
    month_revenue_growth = (
        ((month_revenue - last_month_revenue) / max(last_month_revenue, 1)) * 100
        if last_month_revenue > 0
        else 0
    )

    # Total users and vendors
    total_users = db.scalar(select(func.count(User.id))) or 0
    active_vendors = db.scalar(
        select(func.count(User.id)).where(
            User.vendor_status.in_([VendorStatus.APPROVED])
        )
    ) or 0

    # Conversion rate (estimate: orders / users who have placed at least one order)
    unique_customers = db.scalar(
        select(func.count(func.distinct(Order.user_id)))
    ) or 0
    conversion_rate = (
        (unique_customers / max(total_users, 1)) * 100
        if total_users > 0
        else 0
    )

    # Average order value
    avg_order_value = (
        month_revenue / max(month_orders, 1)
        if month_orders > 0
        else 0
    )

    return {
        "today": {
            "orders": today_orders,
            "revenue": round(float(today_revenue), 2),
        },
        "this_month": {
            "orders": month_orders,
            "revenue": round(float(month_revenue), 2),
            "avg_order_value": round(float(avg_order_value), 2),
            "growth_vs_last_month": {
                "orders_percent": round(month_order_growth, 1),
                "revenue_percent": round(month_revenue_growth, 1),
            },
        },
        "platform": {
            "total_users": total_users,
            "active_vendors": active_vendors,
            "unique_customers": unique_customers,
            "conversion_rate": round(conversion_rate, 2),
        },
    }
