import uuid

from sqlalchemy.orm import Session

from app.models import Order, OrderStatusEvent

# Human-facing copy for each stage of the unified delivery timeline. Kept here
# (rather than duplicated per-controller) so customer, vendor, and admin all
# render the exact same story for a given status.
TIMELINE_STAGE_LABELS: dict[str, str] = {
    "pending_payment": "Order placed",
    "payment_confirmed": "Payment confirmed",
    "pending": "Order received",
    "confirmed": "Confirmed by seller",
    "processing": "Preparing your order",
    "ready": "Ready for handoff",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
}


def record_order_status_event(
    db: Session,
    order: Order,
    *,
    status: str,
    actor_role: str,
    note: str | None = None,
    actor_id: uuid.UUID | None = None,
    event_metadata: dict | None = None,
) -> OrderStatusEvent:
    """Append one immutable timeline entry. Callers still own the commit."""
    event = OrderStatusEvent(
        order_id=order.id,
        status=status,
        actor_role=actor_role,
        note=note,
        actor_id=actor_id,
        event_metadata=event_metadata,
    )
    db.add(event)
    db.flush()
    return event
