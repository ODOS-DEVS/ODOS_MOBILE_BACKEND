from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Order, Product, Review, User
from app.schemas.review import ProductReviewRead, ReviewUpsert, UserReviewRead


def _build_user_display_name(user: User) -> str:
    parts = [part for part in user.full_name.strip().split() if part]
    if not parts:
        return "ODOS Shopper"

    if len(parts) == 1:
        return parts[0]

    return f"{parts[0]} {parts[-1][0]}."


def recompute_product_review_metrics(db: Session, product_id: str) -> None:
    count, avg_rating = db.execute(
        select(func.count(Review.id), func.avg(Review.rating)).where(
            Review.product_id == product_id,
            Review.is_hidden.is_(False),
        )
    ).one()

    product = db.get(Product, product_id)
    if not product:
        return

    if not count:
        product.rating = None
        product.reviews = None
    else:
        product.rating = round(float(avg_rating or 0), 1)
        product.reviews = str(int(count))


def _resolve_review_item_image(
    db: Session,
    *,
    product_id: str,
    order_image_key: str | None,
    order_image_url: str | None,
) -> tuple[str | None, str | None]:
    generic_keys = {"", "bag", "odos", "placeholder"}
    normalized_key = (order_image_key or "").strip().lower()

    if order_image_url:
        return order_image_key, order_image_url

    if normalized_key and normalized_key not in generic_keys:
        return order_image_key, order_image_url

    product = db.get(Product, product_id)
    if not product:
        return order_image_key, order_image_url

    image_key = product.image_key or order_image_key
    image_url = product.image_url or order_image_url
    return image_key, image_url


def _serialize_user_review(db: Session, review: Review) -> UserReviewRead:
    order_item = next(
        (item for item in review.order.items if item.product_id == review.product_id),
        None,
    )

    if order_item is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Review data is missing its matching order item.",
        )

    image_key, image_url = _resolve_review_item_image(
        db,
        product_id=review.product_id,
        order_image_key=order_item.image_key,
        order_image_url=order_item.image_url,
    )

    return UserReviewRead(
        id=review.id,
        order_id=review.order_id,
        order_number=review.order.order_number,
        product_id=review.product_id,
        title=order_item.title,
        category=order_item.category,
        image_key=image_key,
        image_url=image_url,
        rating=review.rating,
        comment=review.comment,
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


def list_product_reviews(
    db: Session,
    product_id: str,
    *,
    limit: int = 20,
) -> list[ProductReviewRead]:
    reviews = list(
        db.scalars(
            select(Review)
            .options(selectinload(Review.user))
            .where(Review.product_id == product_id, Review.is_hidden.is_(False))
            .order_by(Review.updated_at.desc(), Review.created_at.desc())
            .limit(limit)
        ).all()
    )

    return [
        ProductReviewRead(
            id=review.id,
            product_id=review.product_id,
            rating=review.rating,
            comment=review.comment,
            user_display_name=_build_user_display_name(review.user),
            created_at=review.created_at,
            updated_at=review.updated_at,
            vendor_reply=review.vendor_reply,
            vendor_replied_at=review.vendor_replied_at,
        )
        for review in reviews
    ]


def list_user_reviews(db: Session, user: User) -> list[UserReviewRead]:
    reviews = list(
        db.scalars(
            select(Review)
            .options(
                selectinload(Review.order).selectinload(Order.items),
            )
            .where(Review.user_id == user.id)
            .order_by(Review.updated_at.desc(), Review.created_at.desc())
        ).all()
    )

    return [_serialize_user_review(db, review) for review in reviews]


def upsert_review(db: Session, user: User, payload: ReviewUpsert) -> UserReviewRead:
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.id == payload.order_id, Order.user_id == user.id)
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That order was not found.",
        )

    if order.status != "delivered":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only delivered orders can be reviewed.",
        )

    matching_item = next(
        (item for item in order.items if item.product_id == payload.product_id),
        None,
    )
    if matching_item is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That product was not part of the selected order.",
        )

    review = db.scalar(
        select(Review).where(
            Review.user_id == user.id,
            Review.order_id == payload.order_id,
            Review.product_id == payload.product_id,
        )
    )

    if review is None:
        review = Review(
            user_id=user.id,
            order_id=payload.order_id,
            product_id=payload.product_id,
            rating=payload.rating,
            comment=payload.comment,
        )
        db.add(review)
    else:
        review.rating = payload.rating
        review.comment = payload.comment

    db.flush()
    recompute_product_review_metrics(db, payload.product_id)
    db.commit()

    created_review = db.scalar(
        select(Review)
        .options(selectinload(Review.order).selectinload(Order.items))
        .where(Review.id == review.id)
    )
    if not created_review:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="We couldn't reload the review right now.",
        )

    return _serialize_user_review(db, created_review)
