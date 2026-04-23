from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.controllers.auth_controller import google_auth_user, login_user, signup_user
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.user import (
    AuthToken,
    GoogleAuthRequest,
    LogoutResponse,
    UserCreate,
    UserLogin,
    UserRead,
)

router = APIRouter(tags=["auth"])


@router.post(
    "/signup",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    return signup_user(db, user_data)


@router.post("/login", response_model=AuthToken)
def login(
    credentials: UserLogin,
    db: Session = Depends(get_db),
):
    return login_user(db, credentials)


@router.post("/google", response_model=AuthToken)
def google_auth(
    payload: GoogleAuthRequest,
    db: Session = Depends(get_db),
):
    return google_auth_user(db, payload)


@router.get("/me", response_model=UserRead)
def me(current_user: Annotated[User, Depends(get_current_user)]):
    return current_user


@router.post("/logout", response_model=LogoutResponse)
def logout():
    return LogoutResponse(message="Logged out successfully. Remove the token on the app.")
