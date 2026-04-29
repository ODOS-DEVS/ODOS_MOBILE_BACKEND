from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User, WishlistItem
from app.schemas.user import WishlistItemCreate


def list_wishlist_items(db: Session, user: User) -> list[WishlistItem]:
    return list(
        db.scalars(
            select(WishlistItem)
            .where(WishlistItem.user_id == user.id)
            .order_by(WishlistItem.created_at.desc())
        ).all()
    )


def add_wishlist_item(
    db: Session,
    user: User,
    payload: WishlistItemCreate,
) -> WishlistItem:
    existing_item = db.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == user.id,
            WishlistItem.product_id == payload.product_id,
        )
    )
    if existing_item:
        return existing_item

    wishlist_item = WishlistItem(
        user_id=user.id,
        product_id=payload.product_id,
        title=payload.title,
        image_url=payload.image_url,
        category=payload.category,
        price=payload.price,
        old_price=payload.old_price,
        rating=payload.rating,
        reviews=payload.reviews,
    )

    try:
        db.add(wishlist_item)
        db.commit()
        db.refresh(wishlist_item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="We couldn't save that wishlist item right now.",
        ) from None

    return wishlist_item


def remove_wishlist_item(
    db: Session,
    user: User,
    product_id: str,
) -> None:
    wishlist_item = db.scalar(
        select(WishlistItem).where(
            WishlistItem.user_id == user.id,
            WishlistItem.product_id == product_id,
        )
    )

    if not wishlist_item:
        return

    db.delete(wishlist_item)
    db.commit()
