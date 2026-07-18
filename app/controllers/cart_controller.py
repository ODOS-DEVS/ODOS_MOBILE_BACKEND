from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CartItem, Product, User
from app.schemas.user import CartItemCreate, CartItemUpdate


def list_cart_items(db: Session, user: User) -> list[CartItem]:
    return list(
        db.scalars(
            select(CartItem)
            .where(CartItem.user_id == user.id)
            .order_by(CartItem.created_at.desc())
        ).all()
    )


def _assert_product_available_for_cart(
    db: Session,
    *,
    product_id: str,
    quantity: int,
    title: str | None = None,
) -> Product:
    product = db.scalar(select(Product).where(Product.id == product_id))
    label = title or (product.title if product else "That item")
    if (
        not product
        or not product.is_active
        or product.status != "active"
        or product.stock <= 0
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{label} is out of stock.",
        )
    if product.stock < quantity:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Only {product.stock} left in stock for {label}.",
        )
    return product


def add_cart_item(
    db: Session,
    user: User,
    payload: CartItemCreate,
) -> CartItem:
    existing_item = db.scalar(
        select(CartItem).where(
            CartItem.user_id == user.id,
            CartItem.product_id == payload.product_id,
        )
    )
    next_quantity = (
        min(existing_item.quantity + payload.quantity, 99)
        if existing_item
        else payload.quantity
    )
    _assert_product_available_for_cart(
        db,
        product_id=payload.product_id,
        quantity=next_quantity,
        title=payload.title,
    )

    if existing_item:
        existing_item.quantity = next_quantity
        existing_item.title = payload.title
        existing_item.image_url = payload.image_url
        existing_item.image_key = payload.image_key
        existing_item.category = payload.category
        existing_item.price = payload.price
        db.commit()
        db.refresh(existing_item)
        return existing_item

    cart_item = CartItem(
        user_id=user.id,
        product_id=payload.product_id,
        title=payload.title,
        image_url=payload.image_url,
        image_key=payload.image_key,
        category=payload.category,
        price=payload.price,
        quantity=payload.quantity,
    )

    try:
        db.add(cart_item)
        db.commit()
        db.refresh(cart_item)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="We couldn't save that cart item right now.",
        ) from None

    return cart_item


def update_cart_item_quantity(
    db: Session,
    user: User,
    product_id: str,
    payload: CartItemUpdate,
) -> CartItem:
    cart_item = db.scalar(
        select(CartItem).where(
            CartItem.user_id == user.id,
            CartItem.product_id == product_id,
        )
    )
    if not cart_item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That cart item was not found.",
        )

    _assert_product_available_for_cart(
        db,
        product_id=product_id,
        quantity=payload.quantity,
        title=cart_item.title,
    )
    cart_item.quantity = payload.quantity
    db.commit()
    db.refresh(cart_item)
    return cart_item


def remove_cart_item(
    db: Session,
    user: User,
    product_id: str,
) -> None:
    cart_item = db.scalar(
        select(CartItem).where(
            CartItem.user_id == user.id,
            CartItem.product_id == product_id,
        )
    )
    if not cart_item:
        return

    db.delete(cart_item)
    db.commit()


def clear_cart_items(db: Session, user: User) -> None:
    cart_items = list(
        db.scalars(select(CartItem).where(CartItem.user_id == user.id)).all()
    )
    for cart_item in cart_items:
        db.delete(cart_item)
    db.commit()
