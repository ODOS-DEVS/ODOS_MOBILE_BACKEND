"""Vendor handling of return requests raised against their own items.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.controllers.vendor._shared import require_vendor_access
from app.models import (
    OrderItem,
    ReturnRequest,
    User,
)
from app.schemas.vendor import (
    VendorReturnRequestRead,
    VendorReturnRequestUpdate,
)


def _serialize_vendor_return_request(request: ReturnRequest) -> VendorReturnRequestRead:
    order = request.order
    order_item = request.order_item
    return VendorReturnRequestRead(
        id=request.id,
        order_id=request.order_id,
        order_number=order.order_number,
        order_item_id=request.order_item_id,
        product_id=order_item.product_id,
        product_title=order_item.title,
        product_image_url=order_item.image_url,
        customer_name=order.address_full_name,
        request_type=request.request_type,
        status=request.status,
        quantity=request.quantity,
        reason=request.reason,
        details=request.details,
        evidence_image_urls=request.evidence_image_urls,
        admin_note=request.admin_note,
        refund_amount=round(request.refund_amount, 2)
        if request.refund_amount is not None
        else None,
        created_at=request.created_at,
        updated_at=request.updated_at,
    )


def list_vendor_return_requests(db: Session, user: User) -> list[VendorReturnRequestRead]:
    require_vendor_access(user)
    requests = list(
        db.scalars(
            select(ReturnRequest)
            .join(OrderItem, ReturnRequest.order_item_id == OrderItem.id)
            .options(
                selectinload(ReturnRequest.order),
                selectinload(ReturnRequest.order_item),
            )
            .where(OrderItem.vendor_user_id == user.id)
            .order_by(ReturnRequest.created_at.desc())
        ).all()
    )
    return [_serialize_vendor_return_request(request) for request in requests]


def get_vendor_return_request(
    db: Session,
    user: User,
    return_request_id: str,
) -> VendorReturnRequestRead:
    require_vendor_access(user)
    request = db.scalar(
        select(ReturnRequest)
        .join(OrderItem, ReturnRequest.order_item_id == OrderItem.id)
        .options(
            selectinload(ReturnRequest.order),
            selectinload(ReturnRequest.order_item),
        )
        .where(
            ReturnRequest.id == return_request_id,
            OrderItem.vendor_user_id == user.id,
        )
    )
    if not request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That return request was not found.",
        )
    return _serialize_vendor_return_request(request)


def patch_vendor_return_request(
    db: Session,
    user: User,
    return_request_id: str,
    payload: VendorReturnRequestUpdate,
) -> VendorReturnRequestRead:
    from app.services.return_request_service import update_vendor_return_request

    require_vendor_access(user)
    try:
        request = update_vendor_return_request(
            db,
            user,
            return_request_id,
            status=payload.status.strip().lower(),
            vendor_note=payload.vendor_note,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return _serialize_vendor_return_request(request)
