from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.phone import normalize_ghana_phone
from app.models import User, UserVerifiedPhone


def list_verified_phones(db: Session, user: User) -> list[str]:
    phones: set[str] = set()
    if user.phone_verified and user.phone_number:
        phones.add(normalize_ghana_phone(user.phone_number))

    rows = db.scalars(
        select(UserVerifiedPhone.phone).where(UserVerifiedPhone.user_id == user.id)
    ).all()
    phones.update(rows)
    return sorted(phones)


def record_verified_phone(db: Session, user: User, phone_number: str) -> None:
    normalized = normalize_ghana_phone(phone_number)
    existing = db.scalar(
        select(UserVerifiedPhone).where(
            UserVerifiedPhone.user_id == user.id,
            UserVerifiedPhone.phone == normalized,
        )
    )
    if existing:
        return

    db.add(UserVerifiedPhone(user_id=user.id, phone=normalized))


def is_phone_verified_for_user(db: Session, user: User, phone_number: str) -> bool:
    normalized = normalize_ghana_phone(phone_number)
    if user.phone_verified and user.phone_number == normalized:
        return True

    verified = db.scalar(
        select(UserVerifiedPhone.id).where(
            UserVerifiedPhone.user_id == user.id,
            UserVerifiedPhone.phone == normalized,
        )
    )
    return verified is not None
