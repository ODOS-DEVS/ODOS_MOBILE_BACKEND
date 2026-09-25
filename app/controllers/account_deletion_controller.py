"""Endpoints behind the in-app "delete my account" flow."""

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.auth import require_user
from app.core.security import verify_password
from app.models import User
from app.services.account_deletion_service import delete_account, deletion_blockers


class AccountDeletionEligibility(BaseModel):
    can_delete: bool
    blockers: list[str]
    requires_password: bool


class AccountDeletionRequest(BaseModel):
    # Optional because an account created through Google sign-in has no
    # password to confirm. requires_password on the eligibility response tells
    # the client which case it is in.
    password: str | None = None


def get_account_deletion_eligibility(db: Session, current_user: User) -> AccountDeletionEligibility:
    """Report whether this account can be deleted, and what is in the way.

    Offered as its own call so the app can warn someone *before* they work
    through a confirmation flow, rather than refusing at the last step.
    """
    require_user(current_user)
    blockers = deletion_blockers(db, current_user)
    return AccountDeletionEligibility(
        can_delete=not blockers,
        blockers=blockers,
        requires_password=bool(current_user.hashed_password),
    )


def delete_own_account(db: Session, current_user: User, payload: AccountDeletionRequest) -> None:
    """Delete the signed-in user's own account.

    Re-checks the blockers rather than trusting the eligibility call: minutes
    may pass between the two, and an order can be placed in between.
    """
    require_user(current_user)

    # Password holders must re-confirm. A stolen phone should not be enough to
    # destroy someone's account, and this is the same bar Apple expects for a
    # destructive, irreversible action.
    if current_user.hashed_password:
        if not payload.password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter your password to confirm account deletion.",
            )
        if not verify_password(payload.password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="That password is not correct.",
            )

    blockers = deletion_blockers(db, current_user)
    if blockers:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=" ".join(blockers),
        )

    delete_account(db, current_user)
