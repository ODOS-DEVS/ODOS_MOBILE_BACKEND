"""Vendor view of reviews on their products, and their replies.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.controllers.vendor._shared import require_vendor_access
from app.core.admin_pagination import normalize_page_params
from app.models import (
    Product,
    Review,
    User,
)
from app.schemas.vendor import (
    VendorReviewRead,
    VendorReviewReplyUpdate,
)

logger = logging.getLogger(__name__)




def _serialize_vendor_review(review: Review, product: Product, customer: User) -> VendorReviewRead:
    image_url = None
    if product.image_url:
        image_url = product.image_url
    elif product.image_urls:
        image_url = product.image_urls[0] if product.image_urls else None
    return VendorReviewRead(
        id=review.id,
        product_id=product.id,
        product_title=product.title,
        product_image_url=image_url,
        rating=float(review.rating),
        comment=review.comment,
        customer_name=customer.full_name,
        is_hidden=bool(review.is_hidden),
        vendor_reply=review.vendor_reply,
        vendor_replied_at=review.vendor_replied_at,
        created_at=review.created_at,
    )


def list_vendor_reviews(
    db: Session,
    user: User,
    *,
    q: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[VendorReviewRead]:
    require_vendor_access(user)
    resolved_limit, resolved_offset = normalize_page_params(limit, offset)

    statement = (
        select(Review, Product, User)
        .join(Product, Product.id == Review.product_id)
        .join(User, User.id == Review.user_id)
        .where(Product.vendor_user_id == user.id)
    )

    cleaned_query = (q or "").strip()
    if cleaned_query:
        pattern = f"%{cleaned_query}%"
        statement = statement.where(
            or_(
                Product.title.ilike(pattern),
                Review.comment.ilike(pattern),
                User.full_name.ilike(pattern),
            )
        )

    statement = (
        statement.order_by(Review.created_at.desc())
        .offset(resolved_offset)
        .limit(resolved_limit)
    )
    rows = db.execute(statement).all()

    return [
        _serialize_vendor_review(review, product, customer)
        for review, product, customer in rows
    ]


def reply_to_vendor_review(
    db: Session,
    user: User,
    review_id: str,
    payload: VendorReviewReplyUpdate,
) -> VendorReviewRead:
    require_vendor_access(user)
    try:
        normalized_review_id = uuid.UUID(str(review_id))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That review was not found for this vendor.",
        ) from exc

    row = db.execute(
        select(Review, Product, User)
        .join(Product, Product.id == Review.product_id)
        .join(User, User.id == Review.user_id)
        .where(
            Review.id == normalized_review_id,
            Product.vendor_user_id == user.id,
        )
    ).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That review was not found for this vendor.",
        )

    review, product, customer = row
    review.vendor_reply = payload.reply
    review.vendor_replied_at = datetime.now(UTC)
    db.commit()
    db.refresh(review)
    return _serialize_vendor_review(review, product, customer)
