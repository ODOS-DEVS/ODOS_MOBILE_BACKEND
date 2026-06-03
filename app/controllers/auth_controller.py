import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import raise_account_blocked
from app.core.config import settings
from app.core.google_auth import verify_google_identity_token
from app.core.phone import normalize_ghana_phone
from app.core.security import (
    create_access_token,
    create_password_reset_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models import AuthProvider, User, UserAuthAccount
from app.controllers.notification_controller import create_notification_event
from app.schemas.user import (
    AuthToken,
    ForgotPasswordRequest,
    GoogleAuthRequest,
    MessageResponse,
    PasswordResetTokenResponse,
    ResetPasswordRequest,
    UserCreate,
    UserLogin,
    UserUpdate,
    VerifyPasswordResetCodeRequest,
    SendPhoneVerificationRequest,
    VerifyEmailRequest,
    VerifyPhoneRequest,
)
from app.services.email_service import send_email_verification_code, send_password_reset_code
from app.services.arkesel_service import ArkeselSmsError, verify_otp as verify_arkesel_otp
from app.services.phone_verification_service import (
    is_phone_verified_for_user,
    list_verified_phones,
    record_verified_phone,
)
from app.services.sms_service import send_phone_verification_code
from app.services.email_service import (
    send_email_verified_success,
    send_password_changed_success,
)

logger = logging.getLogger(__name__)
EMAIL_VERIFICATION_CODE_LENGTH = 6


def _generate_email_verification_code() -> str:
    return f"{secrets.randbelow(10**EMAIL_VERIFICATION_CODE_LENGTH):0{EMAIL_VERIFICATION_CODE_LENGTH}d}"


def _hash_email_verification_code(email: str, code: str) -> str:
    return hashlib.sha256(
        f"{settings.secret_key}:{email.lower()}:{code}".encode("utf-8")
    ).hexdigest()


def _set_email_verification_code(user: User) -> str:
    code = _generate_email_verification_code()
    user.email_verification_code_hash = _hash_email_verification_code(user.email, code)
    user.email_verification_expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.email_verification_code_expire_minutes
    )
    user.email_verification_sent_at = datetime.now(UTC)
    return code


def _clear_email_verification_code(user: User) -> None:
    user.email_verification_code_hash = None
    user.email_verification_expires_at = None
    user.email_verification_sent_at = None


def _set_password_reset_code(user: User) -> str:
    code = _generate_email_verification_code()
    user.password_reset_code_hash = _hash_email_verification_code(user.email, code)
    user.password_reset_expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.password_reset_code_expire_minutes
    )
    user.password_reset_sent_at = datetime.now(UTC)
    return code


def _clear_password_reset_code(user: User) -> None:
    user.password_reset_code_hash = None
    user.password_reset_expires_at = None
    user.password_reset_sent_at = None


def _hash_phone_verification_code(phone_number: str, code: str) -> str:
    return hashlib.sha256(
        f"{settings.secret_key}:{phone_number}:{code}".encode("utf-8")
    ).hexdigest()


def _set_phone_verification_pending(user: User, phone_number: str) -> None:
    user.phone_verification_code_hash = None
    user.phone_verification_expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.phone_verification_code_expire_minutes
    )
    user.phone_verification_sent_at = datetime.now(UTC)
    user.phone_verification_phone = phone_number


def _set_phone_verification_code(user: User, phone_number: str) -> str:
    code = _generate_email_verification_code()
    user.phone_verification_code_hash = _hash_phone_verification_code(phone_number, code)
    user.phone_verification_expires_at = datetime.now(UTC) + timedelta(
        minutes=settings.phone_verification_code_expire_minutes
    )
    user.phone_verification_sent_at = datetime.now(UTC)
    user.phone_verification_phone = phone_number
    return code


def _clear_phone_verification_code(user: User) -> None:
    user.phone_verification_code_hash = None
    user.phone_verification_expires_at = None
    user.phone_verification_sent_at = None
    user.phone_verification_phone = None


def _dispatch_email_verification_code(
    *,
    user: User,
    code: str,
    strict: bool,
) -> None:
    try:
        send_email_verification_code(
            to_email=user.email,
            to_name=user.full_name,
            code=code,
        )
    except Exception as exc:
        logger.exception("Failed to send verification email to %s", user.email)
        if strict:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="We couldn't send the verification email right now. Please try again.",
            ) from exc


def _dispatch_password_reset_code(
    *,
    user: User,
    code: str,
) -> None:
    try:
        send_password_reset_code(
            to_email=user.email,
            to_name=user.full_name,
            code=code,
        )
    except Exception as exc:
        logger.exception("Failed to send password reset email to %s", user.email)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We couldn't send the reset code right now. Please try again.",
        ) from exc


def _dispatch_email_verified_success(user: User) -> None:
    try:
        send_email_verified_success(
            to_email=user.email,
            to_name=user.full_name,
        )
    except Exception:
        logger.exception(
            "Failed to send email verification success email to %s", user.email
        )


def _dispatch_password_changed_success(user: User) -> None:
    try:
        send_password_changed_success(
            to_email=user.email,
            to_name=user.full_name,
        )
    except Exception:
        logger.exception(
            "Failed to send password changed confirmation email to %s", user.email
        )


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

    code = _set_email_verification_code(user)
    create_notification_event(
        db,
        user,
        kind="account_ready",
        title="Your account is ready",
        body="You can now browse, place orders, and manage everything from your profile.",
        icon="person-outline",
        accent="neutral",
        action_label="Open profile",
        route_type="profile",
        route_target_id=str(user.id),
    )
    db.commit()
    db.refresh(user)
    _dispatch_email_verification_code(user=user, code=code, strict=False)

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
        raise_account_blocked()

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
            raise_account_blocked()
        if avatar_url and not user.avatar_url:
            user.avatar_url = avatar_url
        return build_auth_token(db, user)

    normalized_email = email.lower()
    user = db.scalar(select(User).where(User.email == normalized_email))

    if user:
        if not user.is_active:
            raise_account_blocked()
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


def verify_user_email(
    db: Session,
    user: User,
    payload: VerifyEmailRequest,
) -> User:
    if user.is_verified:
        return user

    if not user.email_verification_code_hash or not user.email_verification_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No verification code is active. Request a new code and try again.",
        )

    if user.email_verification_expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification code has expired. Request a new one and try again.",
        )

    expected_hash = _hash_email_verification_code(user.email, payload.code)
    if not secrets.compare_digest(expected_hash, user.email_verification_code_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That verification code is not correct.",
        )

    user.is_verified = True
    _clear_email_verification_code(user)
    create_notification_event(
        db,
        user,
        kind="email_verified",
        title="Email verified successfully",
        body="Your account is now fully verified and ready for secure shopping.",
        icon="mail-outline",
        accent="success",
        action_label="View profile",
        route_type="profile",
        route_target_id=str(user.id),
    )
    db.commit()
    db.refresh(user)
    _dispatch_email_verified_success(user)
    return user


def resend_verification_code(db: Session, user: User) -> MessageResponse:
    if user.is_verified:
        return MessageResponse(message="This email address is already verified.")

    code = _set_email_verification_code(user)
    db.commit()
    db.refresh(user)
    _dispatch_email_verification_code(user=user, code=code, strict=True)
    return MessageResponse(
        message="We sent a new verification code to your email address."
    )


def request_password_reset(
    db: Session,
    payload: ForgotPasswordRequest,
) -> MessageResponse:
    normalized_email = payload.email.lower()
    user = db.scalar(select(User).where(User.email == normalized_email))

    if not user or not user.is_active or not user.hashed_password:
        return MessageResponse(
            message="If that email is registered, a reset code is on the way."
        )

    code = _set_password_reset_code(user)
    db.commit()
    db.refresh(user)
    _dispatch_password_reset_code(user=user, code=code)
    return MessageResponse(
        message="If that email is registered, a reset code is on the way."
    )


def verify_password_reset_code(
    db: Session,
    payload: VerifyPasswordResetCodeRequest,
) -> PasswordResetTokenResponse:
    normalized_email = payload.email.lower()
    user = db.scalar(select(User).where(User.email == normalized_email))

    if not user or not user.password_reset_code_hash or not user.password_reset_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No reset code is active for this email. Request a new one and try again.",
        )

    if user.password_reset_expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This reset code has expired. Request a new one and try again.",
        )

    expected_hash = _hash_email_verification_code(user.email, payload.code)
    if not secrets.compare_digest(expected_hash, user.password_reset_code_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That reset code is not correct.",
        )

    reset_token = create_password_reset_token(
        subject=str(user.id),
        email=user.email,
    )
    return PasswordResetTokenResponse(
        message="Reset code verified successfully.",
        reset_token=reset_token,
    )


def reset_password(
    db: Session,
    payload: ResetPasswordRequest,
) -> MessageResponse:
    normalized_email = payload.email.lower()
    user = db.scalar(select(User).where(User.email == normalized_email))

    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="We couldn't reset the password for that account.",
        )

    try:
        token_payload = decode_access_token(payload.reset_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset session is no longer valid. Start again.",
        ) from exc

    if token_payload.get("purpose") != "password_reset":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset session is not valid.",
        )

    if token_payload.get("sub") != str(user.id) or token_payload.get("email") != normalized_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This password reset session does not match the account.",
        )

    user.hashed_password = hash_password(payload.new_password)
    _clear_password_reset_code(user)
    create_notification_event(
        db,
        user,
        kind="password_changed",
        title="Password changed successfully",
        body="Your password was updated and your account is secure again.",
        icon="person-outline",
        accent="neutral",
        action_label="Open profile",
        route_type="profile",
        route_target_id=str(user.id),
    )
    db.commit()
    db.refresh(user)
    _dispatch_password_changed_success(user)

    return MessageResponse(message="Your password has been updated successfully.")


def send_phone_verification_code_for_user(
    db: Session,
    user: User,
    payload: SendPhoneVerificationRequest,
) -> MessageResponse:
    phone_number = normalize_ghana_phone(payload.phone_number)

    if user.phone_verified and user.phone_number == phone_number:
        return MessageResponse(message="This phone number is already verified.")

    existing_phone = db.scalar(
        select(User).where(
            User.phone_number == phone_number,
            User.id != user.id,
        )
    )
    if existing_phone:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This phone number is already linked to another account.",
        )

    code = _set_phone_verification_code(user, phone_number)
    db.commit()
    db.refresh(user)

    try:
        send_phone_verification_code(phone_number=phone_number, code=code)
    except Exception as exc:
        logger.exception("Failed to dispatch phone verification SMS")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We couldn't send a verification code right now. Try again shortly.",
        ) from exc

    return MessageResponse(
        message=f"We sent a 6-digit code to {phone_number}."
    )


def verify_user_phone(
    db: Session,
    user: User,
    payload: VerifyPhoneRequest,
) -> User:
    phone_number = normalize_ghana_phone(payload.phone_number)

    if (
        not user.phone_verification_expires_at
        or user.phone_verification_phone != phone_number
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request a new verification code for this number.",
        )

    if user.phone_verification_expires_at < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This verification code has expired. Request a new one.",
        )

    if settings.arkesel_is_configured:
        try:
            verify_arkesel_otp(phone_number=phone_number, code=payload.code)
        except ArkeselSmsError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
    else:
        if not user.phone_verification_code_hash:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request a new verification code for this number.",
            )
        expected_hash = _hash_phone_verification_code(phone_number, payload.code)
        if not secrets.compare_digest(
            expected_hash, user.phone_verification_code_hash
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="That verification code is not correct.",
            )

    if payload.link_to_profile:
        existing_phone = db.scalar(
            select(User).where(
                User.phone_number == phone_number,
                User.id != user.id,
            )
        )
        if existing_phone:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This phone number is already linked to another account.",
            )

        user.phone_number = phone_number
        user.phone_verified = True

    record_verified_phone(db, user, phone_number)
    _clear_phone_verification_code(user)

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This phone number is already linked to another account.",
        ) from None

    return user


def get_user_verified_phones(db: Session, user: User) -> list[str]:
    return list_verified_phones(db, user)


def update_user_profile(db: Session, user: User, payload: UserUpdate) -> User:
    updates = payload.model_dump(exclude_unset=True)

    if "phone_number" in updates:
        requested_phone = updates.pop("phone_number")
        if requested_phone is None or requested_phone == "":
            user.phone_number = None
            user.phone_verified = False
            _clear_phone_verification_code(user)
        elif (
            requested_phone != user.phone_number
            or not user.phone_verified
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Verify your phone number before saving it to your profile.",
            )

    for field, value in updates.items():
        setattr(user, field, value)

    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="We couldn't save those profile changes.",
        ) from None

    return user
