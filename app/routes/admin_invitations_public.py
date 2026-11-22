"""Accepting an administrator invitation. Deliberately unauthenticated.

There is no session yet — the account does not exist until the second call
succeeds. The single-use token is the whole authorisation, and the access being
granted comes from the invitation row rather than from anything the caller
sends, so these endpoints cannot be used to create an administrator with access
nobody approved.

Both responses are intentionally vague about *why* a token failed. "Not valid or
has expired" covers unknown, consumed, revoked and lapsed, so the endpoint
cannot be used to confirm that a particular token or address ever existed.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from app.controllers import admin_rbac_controller as ctl
from app.core.database import get_db
from app.schemas.admin_rbac import InvitationAccept, InvitationPreviewRead
from app.schemas.user import MessageResponse

router = APIRouter(prefix="/admin-invitations", tags=["admin-invitations"])


@router.get("/preview", response_model=InvitationPreviewRead)
def preview_invitation(
    token: str = Query(min_length=10, max_length=256),
    db: Session = Depends(get_db),
):
    """What the invitee is being offered, before they set a password."""
    return ctl.preview_invitation(db, token)


@router.post("/accept", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def accept_invitation(
    payload: InvitationAccept,
    request: Request,
    db: Session = Depends(get_db),
):
    """Create the administrator account and consume the token."""
    user = ctl.accept_invitation(db, payload, request)
    return MessageResponse(
        message=f"Welcome, {user.full_name}. You can now sign in to the ODOS admin panel."
    )
