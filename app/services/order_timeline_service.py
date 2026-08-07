import secrets

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
) -> OrderStatusEvent:
    """Append one immutable timeline entry. Callers still own the commit."""
    event = OrderStatusEvent(
        order_id=order.id,
        status=status,
        actor_role=actor_role,
        note=note,
    )
    db.add(event)
    db.flush()
    return event


def generate_delivery_code() -> str:
    """A short numeric code the customer reads aloud to the vendor/courier at
    handoff — cheap, no-hardware proof of delivery for a vendor-fulfilled
    marketplace (no GPS-tracked rider fleet)."""
    return str(secrets.randbelow(9000) + 1000)


def ensure_delivery_code(order: Order) -> str:
    """Idempotent: an order keeps the same code across retries/re-dispatch."""
    if not order.delivery_code:
        order.delivery_code = generate_delivery_code()
    return order.delivery_code
