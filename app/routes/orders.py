from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.controllers.order_cancellation_controller import (
    decide_cancellation,
    request_cancellation,
)
from app.controllers.order_controller import (
    confirm_order_delivery,
    create_order,
    create_return_request,
    delete_order,
    get_order,
    list_orders,
    report_order_delivery_problem,
    request_order_reschedule,
    submit_order_delivery_rating,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.order import (
    OrderCancellationDecision,
    OrderCancellationRequest,
    OrderCreate,
    OrderDeliveryProblemRequest,
    OrderDeliveryRatingUpdate,
    OrderRead,
    OrderRescheduleRequest,
    ReturnRequestCreate,
    ReturnRequestRead,
)
from app.schemas.user import MessageResponse
from app.services.media_service import save_image_uploads

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=list[OrderRead])
def get_orders(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_orders(db, current_user)


@router.get("/{order_id}", response_model=OrderRead)
def get_order_by_id(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return get_order(db, current_user, order_id)


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_new_order(
    payload: OrderCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return create_order(db, current_user, payload)


@router.patch("/{order_id}/cancel", response_model=OrderRead)
def cancel_existing_order(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Legacy cancel. Kept for older app builds, routed through the new rules.

    The old implementation allowed only unpaid or processing orders and refused
    anything paid, which left a shopper who paid thirty seconds ago with no way
    out. It also overwrote `payment_status`. Both are gone: this now does what
    `/cancellation-request` does, with a default reason.
    """
    return request_cancellation(
        db, current_user, order_id, reason_code="other", note="Cancelled by customer"
    )


@router.post("/{order_id}/cancellation-request", response_model=OrderRead)
def post_cancellation_request(
    order_id: str,
    payload: OrderCancellationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Ask to cancel.

    Completes immediately where policy allows and is held for review where it
    does not — the response carries `cancellation_status` so the client can say
    which happened rather than claiming success either way.
    """
    return request_cancellation(
        db,
        current_user,
        order_id,
        reason_code=payload.reason_code,
        note=payload.note,
    )


@router.post("/{order_id}/cancellation-decision", response_model=OrderRead)
def post_cancellation_decision(
    order_id: str,
    payload: OrderCancellationDecision,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """A vendor answering a held cancellation request on their own order."""
    return decide_cancellation(
        db,
        current_user,
        order_id,
        approve=payload.approve,
        note=payload.note,
        actor_role="vendor",
    )


@router.patch("/{order_id}/deliver", response_model=OrderRead)
def confirm_existing_order_delivery(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Confirm the whole order arrived."""
    return confirm_order_delivery(db, current_user, order_id)


@router.patch("/{order_id}/packages/{package_id}/deliver", response_model=OrderRead)
def confirm_order_package_delivery(
    order_id: str,
    package_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    """Confirm one shop's package arrived.

    A separate path rather than an optional body field on the route above, so
    that confirming a single package is a distinct, auditable action in the
    OpenAPI schema — and so a client cannot accidentally settle three vendors
    by omitting a parameter.
    """
    return confirm_order_delivery(db, current_user, order_id, package_id=package_id)


@router.post("/{order_id}/delivery-problem", response_model=OrderRead)
def report_delivery_problem_route(
    order_id: str,
    payload: OrderDeliveryProblemRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return report_order_delivery_problem(
        db,
        current_user,
        order_id,
        reason=payload.reason,
        details=payload.details,
        package_id=payload.package_id,
    )


@router.patch("/{order_id}/delivery-rating", response_model=OrderRead)
def submit_delivery_rating(
    order_id: str,
    payload: OrderDeliveryRatingUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return submit_order_delivery_rating(db, current_user, order_id, payload.rating)


@router.post("/{order_id}/reschedule", response_model=OrderRead)
def request_delivery_reschedule(
    order_id: str,
    payload: OrderRescheduleRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return request_order_reschedule(
        db, current_user, order_id, payload.note, package_id=payload.package_id
    )


@router.post("/{order_id}/returns", response_model=ReturnRequestRead, status_code=status.HTTP_201_CREATED)
async def create_order_return_request(
    order_id: str,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    content_type = request.headers.get("content-type", "").lower()

    if "multipart/form-data" in content_type:
        form_data = await request.form()
        uploads = [
            upload
            for upload in form_data.getlist("images")
            if getattr(upload, "filename", None)
        ]
        evidence_image_urls = await save_image_uploads(uploads, folder="returns/evidence")
        payload = ReturnRequestCreate.model_validate(
            {
                "order_item_id": form_data.get("order_item_id"),
                "request_type": form_data.get("request_type"),
                "quantity": form_data.get("quantity"),
                "reason": form_data.get("reason"),
                "details": form_data.get("details"),
                "evidence_image_urls": evidence_image_urls or None,
            }
        )
    else:
        payload = ReturnRequestCreate.model_validate(await request.json())

    return create_return_request(db, current_user, order_id, payload)


@router.delete("/{order_id}", response_model=MessageResponse)
def delete_existing_order(
    order_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    delete_order(db, current_user, order_id)
    return MessageResponse(message="Order removed successfully.")
