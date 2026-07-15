from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.notification_controller import (
    build_notification_read_state,
    list_notification_events_page,
    mark_notification_keys_read,
    register_expo_push_token,
    unregister_expo_push_token,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.notification import (
    NotificationEventRead,
    NotificationPageRead,
    NotificationReadState,
    NotificationReadUpdate,
    PushTokenUpdate,
)
from app.schemas.user import UserRead

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/read-state", response_model=NotificationReadState)
def get_notification_read_state(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return NotificationReadState(**build_notification_read_state(db, current_user))


@router.get("", response_model=NotificationPageRead)
def get_notifications(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    limit: int = 25,
    offset: int = 0,
):
    return list_notification_events_page(db, current_user, limit=limit, offset=offset)


@router.post("/read-state", response_model=NotificationReadState)
def mark_notification_read_state(
    payload: NotificationReadUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    mark_notification_keys_read(
        db,
        current_user,
        payload.keys,
        mark_all=payload.mark_all,
    )
    return NotificationReadState(**build_notification_read_state(db, current_user))


@router.post("/push-token", response_model=UserRead)
def save_push_token(
    payload: PushTokenUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return register_expo_push_token(db, current_user, payload.expo_push_token)


@router.delete("/push-token", response_model=UserRead)
def clear_push_token(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return unregister_expo_push_token(db, current_user)
