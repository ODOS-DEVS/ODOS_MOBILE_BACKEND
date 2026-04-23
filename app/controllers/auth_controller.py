from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.google_auth import verify_google_identity_token
from app.core.security import create_access_token, hash_password, verify_password
from app.models import AuthProvider, User, UserAuthAccount
from app.schemas.user import AuthToken, GoogleAuthRequest, UserCreate, UserLogin


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


def login_user(db: Session, credentials: UserLogin) -> AuthToken:
    email = credentials.email.lower()
    user = db.scalar(select(User).where(User.email == email))

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account uses Google sign-in. Please continue with Google.",
        )

    if not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled.",
        )

    return build_auth_token(db, user)


def google_auth_user(db: Session, payload: GoogleAuthRequest) -> AuthToken:
    try:
        google_payload = verify_google_identity_token(payload.id_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    google_sub = google_payload.get("sub")
    email = google_payload.get("email")
    email_verified = bool(google_payload.get("email_verified"))
    full_name = google_payload.get("name") or (email.split("@")[0] if email else "Google User")
    avatar_url = google_payload.get("picture")

    if not google_sub or not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google token is missing required identity fields.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    linked_account = db.scalar(
        select(UserAuthAccount).where(
            UserAuthAccount.provider == AuthProvider.GOOGLE,
            UserAuthAccount.provider_user_id == google_sub,
        )
    )
    if linked_account:
        user = linked_account.user
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account is disabled.",
            )
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url
        return build_auth_token(db, user)

    normalized_email = email.lower()
    user = db.scalar(select(User).where(User.email == normalized_email))

    if user:
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This account is disabled.",
            )
        if not email_verified:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Google account email is not verified.",
            )
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url
        if not user.is_verified:
            user.is_verified = True
    else:
        user = User(
            full_name=full_name,
            email=normalized_email,
            hashed_password=None,
            avatar_url=avatar_url,
            is_verified=email_verified,
        )
        db.add(user)
        db.flush()

    auth_account = UserAuthAccount(
        user_id=user.id,
        provider=AuthProvider.GOOGLE,
        provider_user_id=google_sub,
        provider_email=normalized_email,
    )
    db.add(auth_account)
    db.commit()
    db.refresh(user)

    return build_auth_token(db, user)


def build_auth_token(db: Session, user: User) -> AuthToken:
    user.last_login_at = datetime.now(UTC)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(subject=str(user.id))
    return AuthToken(
        access_token=access_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=user,
    )
