from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import CartItem, Order, ReturnRequest, Store, User, Voucher, VoucherAssignment
from app.models.account import SavedAddress
from app.models.chat import ChatThread, ChatThreadType, SupportChatStatus
from app.controllers.vendor_controller import fetch_vendor_dashboard


@dataclass
class AssistantUserSnapshot:
    latest_order_id: str | None = None
    latest_order_number: str | None = None
    latest_store_id: str | None = None
    latest_vendor_user_id: str | None = None
    best_voucher_code: str | None = None
    is_vendor: bool = False


OPEN_RETURN_STATUSES = ("requested", "pending", "approved", "in_review", "processing")


def _format_money(value: float | str | None) -> str:
    if value is None:
        return "0.00"
    if isinstance(value, str):
        cleaned = value.replace("₵", "").replace("GHS", "").strip()
        try:
            return f"{float(cleaned):.2f}"
        except ValueError:
            return cleaned or "0.00"
    return f"{float(value):.2f}"


def build_assistant_user_context(
    db: Session,
    user: User | None,
) -> tuple[str, AssistantUserSnapshot | None]:
    if user is None:
        active_store_count = int(
            db.scalar(
                select(func.count())
                .select_from(Store)
                .where(
                    Store.is_active.is_(True),
                    Store.status == "active",
                    Store.is_on_vacation.is_(False),
                )
            )
            or 0
        )
        return (
            "User is browsing as a guest (not signed in). "
            "They cannot see personal orders, vouchers, or wallet details until they sign in. "
            f"Marketplace: {active_store_count} active stores on ODOS.",
            None,
        )

    snapshot = AssistantUserSnapshot(
        is_vendor="vendor" in (user.roles or []),
    )
    lines = [
        f"Signed-in user: {user.full_name or user.email}",
        f"Roles: {', '.join(user.roles or ['customer'])}",
    ]

    active_store_count = int(
        db.scalar(
            select(func.count())
            .select_from(Store)
            .where(
                Store.is_active.is_(True),
                Store.status == "active",
                Store.is_on_vacation.is_(False),
            )
        )
        or 0
    )
    lines.append(f"Marketplace: {active_store_count} active stores on ODOS")

    default_address = db.scalar(
        select(SavedAddress)
        .where(SavedAddress.user_id == user.id, SavedAddress.is_default.is_(True))
        .limit(1)
    )
    if default_address is None:
        default_address = db.scalar(
            select(SavedAddress)
            .where(SavedAddress.user_id == user.id)
            .order_by(desc(SavedAddress.updated_at))
            .limit(1)
        )
    if default_address:
        lines.append(
            "Default delivery address: "
            f"{default_address.city}, {default_address.region} "
            f"({default_address.street})"
        )

    cart_items = list(
        db.scalars(
            select(CartItem)
            .where(CartItem.user_id == user.id)
            .order_by(desc(CartItem.updated_at))
            .limit(5)
        ).all()
    )
    if cart_items:
        lines.append("Cart items:")
        for item in cart_items:
            lines.append(
                f"- {item.title} x{item.quantity} · GH₵{_format_money(item.price)} "
                f"(product_id={item.product_id})"
            )
    else:
        lines.append("Cart items: none")

    recent_orders = list(
        db.scalars(
            select(Order)
            .where(Order.user_id == user.id)
            .options(selectinload(Order.items))
            .order_by(desc(Order.created_at))
            .limit(3)
        ).all()
    )
    order_count = int(
        db.scalar(select(func.count()).select_from(Order).where(Order.user_id == user.id)) or 0
    )
    lines.append(f"Total orders: {order_count}")
    if recent_orders:
        lines.append("Recent orders (newest first):")
        for order in recent_orders:
            item_titles = ", ".join(item.title for item in order.items[:3]) or "Items unavailable"
            store_ids = {item.store_id for item in order.items if item.store_id}
            vendor_ids = {str(item.vendor_user_id) for item in order.items if item.vendor_user_id}
            eta = f", ETA {order.tracking_eta}" if order.tracking_eta else ""
            lines.append(
                f"- #{order.order_number} (order_id={order.id}) · status {order.status} · "
                f"GH₵{order.total_amount:.2f}{eta} · items: {item_titles}"
            )
            if store_ids:
                lines.append(f"  store_ids: {', '.join(sorted(store_ids))}")
            if vendor_ids:
                lines.append(f"  vendor_user_ids: {', '.join(sorted(vendor_ids))}")

        latest = recent_orders[0]
        snapshot.latest_order_id = str(latest.id)
        snapshot.latest_order_number = latest.order_number
        if latest.items:
            first_item = latest.items[0]
            snapshot.latest_store_id = first_item.store_id
            snapshot.latest_vendor_user_id = (
                str(first_item.vendor_user_id) if first_item.vendor_user_id else None
            )

    now = datetime.now(timezone.utc)
    wallet_vouchers = list(
        db.scalars(
            select(Voucher)
            .join(VoucherAssignment, VoucherAssignment.voucher_id == Voucher.id)
            .where(
                VoucherAssignment.user_id == user.id,
                Voucher.is_active.is_(True),
                Voucher.approval_status == "approved",
                or_(Voucher.ends_at.is_(None), Voucher.ends_at > now),
            )
            .order_by(Voucher.ends_at.asc().nulls_last(), Voucher.created_at.desc())
            .limit(5)
        ).all()
    )
    if wallet_vouchers:
        lines.append("Saved vouchers:")
        for voucher in wallet_vouchers:
            expiry = (
                voucher.ends_at.astimezone(timezone.utc).strftime("%Y-%m-%d")
                if voucher.ends_at
                else "no expiry"
            )
            lines.append(
                f"- {voucher.code} · {voucher.title} · {voucher.reward_text} · expires {expiry}"
            )
        snapshot.best_voucher_code = wallet_vouchers[0].code
    else:
        lines.append("Saved vouchers: none")

    open_returns = list(
        db.scalars(
            select(ReturnRequest)
            .where(
                ReturnRequest.user_id == user.id,
                ReturnRequest.status.in_(OPEN_RETURN_STATUSES),
            )
            .order_by(desc(ReturnRequest.created_at))
            .limit(3)
        ).all()
    )
    if open_returns:
        lines.append("Open return requests:")
        for request in open_returns:
            lines.append(
                f"- return_id={request.id} · order_id={request.order_id} · "
                f"status {request.status} · reason {request.reason}"
            )
    else:
        lines.append("Open return requests: none")

    open_support_chats = int(
        db.scalar(
            select(func.count())
            .select_from(ChatThread)
            .where(
                ChatThread.customer_user_id == user.id,
                ChatThread.thread_type == ChatThreadType.SUPPORT,
                ChatThread.support_status != SupportChatStatus.RESOLVED,
            )
        )
        or 0
    )
    if open_support_chats:
        lines.append(f"Open support chats: {open_support_chats}")

    if snapshot.is_vendor:
        lines.append(
            "User has vendor access — prioritize vendor dashboard, pending orders, products, payouts, and returns."
        )
        try:
            dashboard = fetch_vendor_dashboard(db, user)
            lines.extend(
                [
                    "Vendor dashboard (live):",
                    f"- Store: {dashboard.store_name}",
                    f"- Pending orders: {dashboard.pending_orders}",
                    f"- Active products: {dashboard.active_products} / {dashboard.total_products}",
                    f"- Available balance: GH₵{dashboard.available_balance:.2f}",
                    f"- Lifetime earnings: GH₵{dashboard.lifetime_earnings:.2f}",
                    f"- Completed orders: {dashboard.completed_orders}",
                ]
            )
        except Exception:
            lines.append("Vendor dashboard: unavailable (store may not be fully set up).")

    return "\n".join(lines), snapshot
