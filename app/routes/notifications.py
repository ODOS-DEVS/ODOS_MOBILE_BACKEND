from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.controllers.notification_controller import (
    list_notification_events,
    list_notification_read_keys,
    mark_notification_keys_read,
    register_expo_push_token,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.notification import (
    NotificationEventRead,
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
    return NotificationReadState(
        read_keys=list_notification_read_keys(db, current_user)
    )


@router.get("", response_model=list[NotificationEventRead])
def get_notifications(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_notification_events(db, current_user)


@router.post("/read-state", response_model=NotificationReadState)
def mark_notification_read_state(
    payload: NotificationReadUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return NotificationReadState(
        read_keys=mark_notification_keys_read(db, current_user, payload.keys)
    )


@router.post("/push-token", response_model=UserRead)
def save_push_token(
    payload: PushTokenUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return register_expo_push_token(db, current_user, payload.expo_push_token)
