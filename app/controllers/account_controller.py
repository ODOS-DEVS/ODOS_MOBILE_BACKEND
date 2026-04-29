from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SavedAddress, SavedPaymentMethod, User
from app.schemas.account import AddressCreate, AddressUpdate, PaymentMethodCreate


def list_addresses(db: Session, user: User) -> list[SavedAddress]:
    return list(
        db.scalars(
            select(SavedAddress)
            .where(SavedAddress.user_id == user.id)
            .order_by(SavedAddress.is_default.desc(), SavedAddress.updated_at.desc())
        ).all()
    )


def create_address(db: Session, user: User, payload: AddressCreate) -> SavedAddress:
    current_addresses = list_addresses(db, user)
    should_default = payload.is_default or len(current_addresses) == 0

    if should_default:
        for address in current_addresses:
            address.is_default = False

    address = SavedAddress(
        user_id=user.id,
        **payload.model_dump(exclude={"is_default"}),
        is_default=should_default,
    )
    db.add(address)
    db.commit()
    db.refresh(address)
    return address


def update_address(db: Session, user: User, address_id: str, payload: AddressUpdate) -> SavedAddress:
    address = db.scalar(
        select(SavedAddress).where(SavedAddress.id == address_id, SavedAddress.user_id == user.id)
    )
    if not address:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That address was not found.")

    data = payload.model_dump(exclude_unset=True)
    should_default = data.pop("is_default", None)

    for key, value in data.items():
        setattr(address, key, value)

    if should_default:
        for current in list_addresses(db, user):
            current.is_default = current.id == address.id

    db.commit()
    db.refresh(address)
    return address


def delete_address(db: Session, user: User, address_id: str) -> None:
    address = db.scalar(
        select(SavedAddress).where(SavedAddress.id == address_id, SavedAddress.user_id == user.id)
    )
    if not address:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That address was not found.")

    was_default = address.is_default
    db.delete(address)
    db.commit()

    if was_default:
        remaining = list_addresses(db, user)
        if remaining:
            remaining[0].is_default = True
            db.commit()


def set_default_address(db: Session, user: User, address_id: str) -> SavedAddress:
    addresses = list_addresses(db, user)
    target = next((address for address in addresses if str(address.id) == address_id), None)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That address was not found.")

    for address in addresses:
        address.is_default = address.id == target.id

    db.commit()
    db.refresh(target)
    return target


def list_payment_methods(db: Session, user: User) -> list[SavedPaymentMethod]:
    return list(
        db.scalars(
            select(SavedPaymentMethod)
            .where(SavedPaymentMethod.user_id == user.id)
            .order_by(SavedPaymentMethod.is_default.desc(), SavedPaymentMethod.updated_at.desc())
        ).all()
    )


def create_payment_method(db: Session, user: User, payload: PaymentMethodCreate) -> SavedPaymentMethod:
    current_methods = list_payment_methods(db, user)
    should_default = payload.is_default or len(current_methods) == 0

    if should_default:
        for method in current_methods:
            method.is_default = False

    digits = "".join(character for character in (payload.card_number or "") if character.isdigit())
    card_last4 = digits[-4:] if digits else None
    label = payload.label or (f"**** {card_last4}" if payload.type == "card" else f"{payload.network} MoMo")

    payment_method = SavedPaymentMethod(
        user_id=user.id,
        type=payload.type,
        label=label,
        is_default=should_default,
        card_name=payload.card_name,
        card_last4=card_last4,
        expiry=payload.expiry,
        network=payload.network,
        phone=payload.phone,
    )
    db.add(payment_method)
    db.commit()
    db.refresh(payment_method)
    return payment_method


def delete_payment_method(db: Session, user: User, payment_method_id: str) -> None:
    payment_method = db.scalar(
        select(SavedPaymentMethod).where(
            SavedPaymentMethod.id == payment_method_id,
            SavedPaymentMethod.user_id == user.id,
        )
    )
    if not payment_method:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That payment method was not found.")

    was_default = payment_method.is_default
    db.delete(payment_method)
    db.commit()

    if was_default:
        remaining = list_payment_methods(db, user)
        if remaining:
            remaining[0].is_default = True
            db.commit()


def set_default_payment_method(db: Session, user: User, payment_method_id: str) -> SavedPaymentMethod:
    payment_methods = list_payment_methods(db, user)
    target = next((item for item in payment_methods if str(item.id) == payment_method_id), None)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="That payment method was not found.")

    for payment_method in payment_methods:
        payment_method.is_default = payment_method.id == target.id

    db.commit()
    db.refresh(target)
    return target
