from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.event_types import (
    ADMIN_INVENTORY_CHANGED,
    ADMIN_ORDER_MUTATION,
    ADMIN_PRICE_CHANGED,
    ADMIN_PRODUCT_MUTATION,
    ADMIN_ROLE_CHANGED,
    ADMIN_USER_MUTATION,
    ADMIN_VENDOR_MUTATION,
)
from app.models import User
from app.services.event_log_service import record_admin_event


def log_admin_user_status_change(
    db: Session,
    *,
    admin_user: User,
    target_user: User,
    before_active: bool,
    after_active: bool,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    record_admin_event(
        db,
        admin_user=admin_user,
        event_type=ADMIN_USER_MUTATION,
        action="user.account_status_updated",
        entity_type="user",
        entity_id=str(target_user.id),
        before_state={"is_active": before_active},
        after_state={"is_active": after_active},
        metadata={"email": target_user.email},
        ip_address=ip_address,
        user_agent=user_agent,
    )


def log_admin_vendor_status_change(
    db: Session,
    *,
    admin_user: User,
    vendor: User,
    before_status: str,
    after_status: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    record_admin_event(
        db,
        admin_user=admin_user,
        event_type=ADMIN_VENDOR_MUTATION,
        action="vendor.status_updated",
        entity_type="vendor",
        entity_id=str(vendor.id),
        before_state={"vendor_status": before_status},
        after_state={"vendor_status": after_status},
        metadata={"email": vendor.email},
        ip_address=ip_address,
        user_agent=user_agent,
    )


def log_admin_order_status_change(
    db: Session,
    *,
    admin_user: User,
    order_id: str,
    order_number: str | None,
    before_status: str,
    after_status: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    record_admin_event(
        db,
        admin_user=admin_user,
        event_type=ADMIN_ORDER_MUTATION,
        action="order.status_updated",
        entity_type="order",
        entity_id=order_id,
        before_state={"vendor_status": before_status},
        after_state={"vendor_status": after_status},
        metadata={"order_number": order_number},
        ip_address=ip_address,
        user_agent=user_agent,
    )


def log_admin_product_mutation(
    db: Session,
    *,
    admin_user: User,
    action: str,
    product_id: str,
    before_state: dict[str, Any] | None = None,
    after_state: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    event_type = ADMIN_PRODUCT_MUTATION
    if before_state and after_state:
        price_keys = {"price", "sale_price", "compare_at_price"}
        if price_keys.intersection(before_state.keys()) or price_keys.intersection(after_state.keys()):
            event_type = ADMIN_PRICE_CHANGED
        inventory_keys = {"stock", "inventory", "quantity", "stock_quantity"}
        if inventory_keys.intersection(before_state.keys()) or inventory_keys.intersection(after_state.keys()):
            event_type = ADMIN_INVENTORY_CHANGED

    record_admin_event(
        db,
        admin_user=admin_user,
        event_type=event_type,
        action=action,
        entity_type="product",
        entity_id=product_id,
        before_state=before_state,
        after_state=after_state,
        metadata=metadata,
        ip_address=ip_address,
        user_agent=user_agent,
    )


def log_admin_role_change(
    db: Session,
    *,
    admin_user: User,
    target_user: User,
    before_permission: str | None,
    after_permission: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    record_admin_event(
        db,
        admin_user=admin_user,
        event_type=ADMIN_ROLE_CHANGED,
        action="admin.permission_updated",
        entity_type="user",
        entity_id=str(target_user.id),
        before_state={"admin_permission": before_permission},
        after_state={"admin_permission": after_permission},
        metadata={"email": target_user.email},
        ip_address=ip_address,
        user_agent=user_agent,
    )


def log_admin_return_resolution(
    db: Session,
    *,
    admin_user: User,
    return_request_id: str,
    order_number: str | None,
    before_status: str,
    after_status: str,
    refund_amount: float | None,
    waived: bool,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Audit a return that moved money.

    Approving and refunding are the two points where a return costs somebody
    something, and until now neither left an audit entry — the request record
    itself only keeps the latest reviewer, overwritten on each change.
    """
    record_admin_event(
        db,
        admin_user=admin_user,
        event_type=ADMIN_ORDER_MUTATION,
        action=f"return.{after_status}",
        entity_type="return_request",
        entity_id=return_request_id,
        before_state={"status": before_status},
        after_state={"status": after_status, "refund_amount": refund_amount},
        metadata={"order_number": order_number, "return_waived": waived},
        ip_address=ip_address,
        user_agent=user_agent,
    )
