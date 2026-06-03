from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.controllers.auth_controller import (
    google_auth_user,
    login_user,
    request_password_reset,
    resend_verification_code,
    reset_password,
    get_user_verified_phones,
    send_phone_verification_code_for_user,
    signup_user,
    update_user_profile,
    verify_password_reset_code,
    verify_user_email,
    verify_user_phone,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.core.rate_limit import (
    limit_forgot_password,
    limit_google_auth,
    limit_login,
    limit_resend_email_verification,
    limit_reset_password,
    limit_send_phone_code,
    limit_signup,
    limit_verify_email,
    limit_verify_phone,
    limit_verify_reset_code,
)
from app.models import User
from app.schemas.user import (
    AuthToken,
    ForgotPasswordRequest,
    GoogleAuthRequest,
    LogoutResponse,
    MessageResponse,
    PasswordResetTokenResponse,
    ResetPasswordRequest,
    UserCreate,
    UserLogin,
    UserRead,
    UserUpdate,
    VerifyPasswordResetCodeRequest,
    SendPhoneVerificationRequest,
    VerifyEmailRequest,
    VerifiedPhonesResponse,
    VerifyPhoneRequest,
)

router = APIRouter(tags=["auth"])


@router.post(
    "/signup",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def signup(
    user_data: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    limit_signup(request)
    return signup_user(db, user_data)


@router.post("/login", response_model=AuthToken)
async def login(
    request: Request,
    db: Session = Depends(get_db),
):
    content_type = request.headers.get("content-type", "").lower()

    try:
        if (
            "application/x-www-form-urlencoded" in content_type
            or "multipart/form-data" in content_type
        ):
            form_data = await request.form()
            credentials = UserLogin(
                email=str(form_data.get("username", "")),
                password=str(form_data.get("password", "")),
            )
        else:
            payload = await request.json()
            credentials = UserLogin.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc

    limit_login(request, credentials.email)
    return login_user(db, credentials)


@router.post("/google", response_model=AuthToken)
def google_auth(
    payload: GoogleAuthRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    limit_google_auth(request)
    return google_auth_user(db, payload)


@router.post("/verify-email", response_model=UserRead)
def verify_email(
    payload: VerifyEmailRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    limit_verify_email(current_user)
    return verify_user_email(db, current_user, payload)


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    limit_forgot_password(request, payload.email)
    return request_password_reset(db, payload)


@router.post("/verify-reset-code", response_model=PasswordResetTokenResponse)
def verify_reset_code(
    payload: VerifyPasswordResetCodeRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    limit_verify_reset_code(request, payload.email)
    return verify_password_reset_code(db, payload)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password_route(
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    limit_reset_password(request)
    return reset_password(db, payload)


@router.post("/resend-verification-code", response_model=MessageResponse)
def resend_email_verification_code(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    limit_resend_email_verification(current_user)
    return resend_verification_code(db, current_user)


@router.post("/phone/send-code", response_model=MessageResponse)
def send_phone_verification_code_route(
    payload: SendPhoneVerificationRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    limit_send_phone_code(current_user, payload.phone_number)
    return send_phone_verification_code_for_user(db, current_user, payload)


@router.post("/phone/verify", response_model=UserRead)
def verify_phone_route(
    payload: VerifyPhoneRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    limit_verify_phone(current_user)
    return verify_user_phone(db, current_user, payload)


@router.get("/phone/verified", response_model=VerifiedPhonesResponse)
def list_verified_phones_route(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return VerifiedPhonesResponse(phones=get_user_verified_phones(db, current_user))


@router.get("/me", response_model=UserRead)
def me(current_user: Annotated[User, Depends(get_current_user)]):
    return current_user


@router.patch("/me", response_model=UserRead)
def update_me(
    payload: UserUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return update_user_profile(db, current_user, payload)


@router.post("/logout", response_model=LogoutResponse)
def logout():
    return LogoutResponse(
        message="Logged out successfully. Remove the token on the app."
    )
