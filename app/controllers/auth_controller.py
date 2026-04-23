from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models import User
from app.schemas.user import UserCreate


def signup_user(db: Session, user_data: UserCreate) -> User:
    email = user_data.email.lower()
    phone_number = user_data.phone_number

    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists.",
        )

    if phone_number:
        existing_phone = db.scalar(
            select(User).where(User.phone_number == phone_number)
        )
        if existing_phone:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this phone number already exists.",
            )

    user = User(
        full_name=user_data.full_name,
        email=email,
        phone_number=phone_number,
        hashed_password=hash_password(user_data.password),
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with these details already exists.",
        ) from None

    return user
