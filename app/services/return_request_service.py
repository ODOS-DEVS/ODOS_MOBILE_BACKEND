"""Shared return-request status transitions for admin and vendor flows —
the single place that money actually moves for a return, and the single
place that decides whether a status transition is legal. Both the admin
and vendor endpoints delegate here rather than keeping their own copies,
so the state machine and the concurrency guard below can't drift out of
sync between the two roles."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.customer_wallet_controller import credit_customer_wallet_for_return
from app.controllers.finance_controller import record_refund_adjustments
from app.controllers.notification_controller import create_notification_event
from app.controllers.wallet_controller import (
    publish_vendor_wallet_updates,
    reverse_vendor_wallet_for_return_request,
)
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

TERMINAL_RETURN_STATUSES = {"rejected", "refunded", "exchanged"}

# What a request may move to *from* its current status. Non-terminal statuses
# may also stay put (e.g. an admin editing just the note) — terminal ones may
# not, which is what actually stops the same request from being refunded (or
# rejected, or exchanged) more than once.
ALLOWED_RETURN_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"requested", "under_review", "approved", "rejected", "refunded", "exchanged"},
    "under_review": {"under_review", "approved", "rejected", "refunded", "exchanged"},
    "approved": {"approved", "rejected", "refunded", "exchanged"},
    "rejected": set(),
    "refunded": set(),
    "exchanged": set(),
}

_STATUS_COPY = {
    "requested": "Return request reopened",
    "under_review": "Return request under review",
    "approved": "Return approved",
    "rejected": "Return request declined",
    "refunded": "Refund completed",
    "exchanged": "Exchange completed",
}


class ReturnRequestError(ValueError):
    """Base class for return-request status-change failures. Callers can
    catch this alone (instead of the specific subclasses) and always get a
    safe, user-facing message via str(exc)."""


class ReturnRequestNotFoundError(ReturnRequestError):
    def __init__(self) -> None:
        super().__init__("That return request was not found.")


class ReturnRequestIllegalTransitionError(ReturnRequestError):
    def __init__(self, current_status: str, next_status: str) -> None:
        self.current_status = current_status
        self.next_status = next_status
        if current_status in TERMINAL_RETURN_STATUSES:
            message = (
                f"This return request is already {current_status.replace('_', ' ')} "
                "and can't be changed again."
            )
        else:
            message = (
                f"Can't move a return request from {current_status.replace('_', ' ')} "
                f"to {next_status.replace('_', ' ')}."
            )
        super().__init__(message)


@dataclass
class ReturnRequestChangeResult:
    request: ReturnRequest
    changed_wallet_vendor_id: uuid.UUID | None


def _load_return_request_for_update(db: Session, request_id: str) -> ReturnRequest | None:
    """Row-locks the return request for the rest of the caller's transaction.

    This is the serialization point that stops two concurrent callers — an
    admin and a vendor acting on the same request, or a doubled-up/retried
    request — from both processing the same refund. The second caller blocks
    here until the first commits, then re-reads the now-updated status and is
    rejected by the transition check below instead of moving money twice."""
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
        .with_for_update()
    )


def apply_return_request_status_change(
    db: Session,
    request_id: str,
    *,
    status: str,
    note: str | None,
    refund_amount: float | None,
    reviewed_by_user_id: uuid.UUID,
    max_refund_amount: float | None = None,
) -> ReturnRequestChangeResult:
    if status not in SUPPORTED_RETURN_REQUEST_STATUSES:
        raise ReturnRequestError("Unsupported return request status.")

    request = _load_return_request_for_update(db, request_id)
    if not request:
        raise ReturnRequestNotFoundError()

    allowed_next = ALLOWED_RETURN_STATUS_TRANSITIONS.get(request.status, set())
    if status not in allowed_next:
        raise ReturnRequestIllegalTransitionError(request.status, status)

    # Only a genuine first-time move into "refunded" should ever touch money —
    # without this, re-saving an already-refunded request (blocked above in
    # practice, but kept as an explicit guard here too) could double-credit.
    is_new_refund = status == "refunded" and request.status != "refunded"

    request.status = status
    if note is not None:
        request.admin_note = note
    request.reviewed_by_user_id = reviewed_by_user_id
    request.reviewed_at = datetime.now(UTC)

    if refund_amount is not None:
        if max_refund_amount is not None and refund_amount > max_refund_amount:
            raise ReturnRequestError(
                f"Refund amount can't exceed the item's value ({max_refund_amount})."
            )
        request.refund_amount = round(refund_amount, 2)
    elif status == "refunded" and request.refund_amount is None:
        fallback_amount = max_refund_amount
        if fallback_amount is None:
            fallback_amount = request.order_item.unit_price * request.quantity
        request.refund_amount = round(fallback_amount, 2)

    request.resolved_at = datetime.now(UTC) if status in TERMINAL_RETURN_STATUSES else None

    changed_wallet_vendor_id: uuid.UUID | None = None
    if is_new_refund:
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

    from app.controllers.order_controller import _broadcast_order_realtime

    _broadcast_order_realtime(db, request.order)

    return ReturnRequestChangeResult(
        request=request,
        changed_wallet_vendor_id=changed_wallet_vendor_id,
    )


def update_vendor_return_request(
    db: Session,
    user: User,
    return_request_id: str,
    *,
    status: str,
    vendor_note: str | None,
) -> ReturnRequest:
    if status not in VENDOR_RETURN_REQUEST_STATUSES:
        raise ReturnRequestError("That return status is not supported for vendors.")

    owned_request_id = db.scalar(
        select(ReturnRequest.id)
        .join(OrderItem, ReturnRequest.order_item_id == OrderItem.id)
        .where(
            ReturnRequest.id == return_request_id,
            OrderItem.vendor_user_id == user.id,
        )
    )
    if not owned_request_id:
        raise ReturnRequestNotFoundError()

    result = apply_return_request_status_change(
        db,
        return_request_id,
        status=status,
        note=vendor_note,
        refund_amount=None,
        reviewed_by_user_id=user.id,
    )
    return result.request
