"""Shared return-request status transitions for admin and vendor flows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select

from app.controllers.customer_wallet_controller import credit_customer_wallet_for_return
from app.controllers.finance_controller import record_refund_adjustments
from app.controllers.notification_controller import create_notification_event
from app.controllers.order_controller import _broadcast_order_realtime
from app.controllers.wallet_controller import publish_vendor_wallet_updates, reverse_vendor_wallet_for_return_request
from app.models import Order, OrderItem, ReturnRequest, User
from app.services.push_service import dispatch_customer_return_push

SUPPORTED_RETURN_REQUEST_STATUSES = {
    "requested",
    "under_review",
    "approved",
    "rejected",
    "refunded",
    "exchanged",
}

VENDOR_RETURN_REQUEST_STATUSES = {
    "under_review",
    "approved",
    "rejected",
    "refunded",
}

_STATUS_COPY = {
    "requested": "Return request reopened",
    "under_review": "Return request under review",
    "approved": "Return approved",
    "rejected": "Return request declined",
    "refunded": "Refund completed",
    "exchanged": "Exchange completed",
}


def _load_return_request(db: Session, request_id: str) -> ReturnRequest | None:
    return db.scalar(
        select(ReturnRequest)
        .options(
            selectinload(ReturnRequest.order).selectinload(Order.items),
            selectinload(ReturnRequest.order).selectinload(Order.user),
            selectinload(ReturnRequest.order).selectinload(Order.return_requests),
            selectinload(ReturnRequest.order_item),
            selectinload(ReturnRequest.reviewed_by_user),
        )
        .where(ReturnRequest.id == request_id)
    )


def apply_return_request_status_change(
    db: Session,
    request: ReturnRequest,
    *,
    status: str,
    note: str | None,
    refund_amount: float | None,
    reviewed_by_user_id: uuid.UUID | None,
) -> uuid.UUID | None:
    if status not in SUPPORTED_RETURN_REQUEST_STATUSES:
        raise ValueError("Unsupported return request status.")

    request.status = status
    if note is not None:
        request.admin_note = note
    if reviewed_by_user_id is not None:
        request.reviewed_by_user_id = reviewed_by_user_id
        request.reviewed_at = datetime.now(UTC)

    if refund_amount is not None:
        request.refund_amount = round(refund_amount, 2)
    elif status == "refunded" and request.refund_amount is None:
        request.refund_amount = round(
            request.order_item.unit_price * request.quantity,
            2,
        )

    if status in {"rejected", "refunded", "exchanged"}:
        request.resolved_at = datetime.now(UTC)
    elif status in {"requested", "under_review", "approved"}:
        request.resolved_at = None

    changed_wallet_vendor_id: uuid.UUID | None = None
    if status == "refunded":
        changed_wallet_vendor_id = reverse_vendor_wallet_for_return_request(db, request)
        record_refund_adjustments(db, request)
        credit_customer_wallet_for_return(db, request)

    title = _STATUS_COPY.get(status, "Return request updated")
    body = f"{request.order_item.title}: {status.replace('_', ' ')}."
    return_event = create_notification_event(
        db,
        request.order.user,
        kind="return_updated",
        title=title,
        body=body,
        icon="swap-horizontal-outline",
        accent=(
            "warning"
            if status in {"requested", "under_review"}
            else "success"
            if status in {"approved", "refunded", "exchanged"}
            else "warning"
        ),
        action_label="View order",
        route_type="order",
        route_target_id=str(request.order_id),
        image_key=request.order_item.image_key,
    )
    dispatch_customer_return_push(
        user=request.order.user,
        title=title,
        body=body,
        order_id=request.order_id,
        notification_event=return_event,
    )
    db.commit()
    db.refresh(request)

    if changed_wallet_vendor_id:
        publish_vendor_wallet_updates(changed_wallet_vendor_id)

    _broadcast_order_realtime(db, request.order)
    return changed_wallet_vendor_id


def update_vendor_return_request(
    db: Session,
    user: User,
    return_request_id: str,
    *,
    status: str,
    vendor_note: str | None,
) -> ReturnRequest:
    if status not in VENDOR_RETURN_REQUEST_STATUSES:
        raise ValueError("That return status is not supported for vendors.")

    request = db.scalar(
        select(ReturnRequest)
        .join(OrderItem, ReturnRequest.order_item_id == OrderItem.id)
        .options(
            selectinload(ReturnRequest.order).selectinload(Order.user),
            selectinload(ReturnRequest.order).selectinload(Order.return_requests),
            selectinload(ReturnRequest.order_item),
        )
        .where(
            ReturnRequest.id == return_request_id,
            OrderItem.vendor_user_id == user.id,
        )
    )
    if not request:
        raise ValueError("That return request was not found.")

    if status == "refunded" and request.status not in {"approved", "under_review", "requested"}:
        raise ValueError("Only approved returns can be marked refunded.")

    apply_return_request_status_change(
        db,
        request,
        status=status,
        note=vendor_note,
        refund_amount=None,
        reviewed_by_user_id=user.id,
    )
    refreshed = _load_return_request(db, return_request_id)
    if not refreshed:
        raise ValueError("That return request was not found.")
    return refreshed
