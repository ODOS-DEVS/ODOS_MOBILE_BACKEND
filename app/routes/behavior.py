from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.controllers.behavior_controller import record_behavior_event_batch
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.behavior import BehaviorEventBatchCreate, BehaviorEventBatchRead

router = APIRouter(prefix="/behavior", tags=["behavior"])


@router.post(
    "/events/batch",
    response_model=BehaviorEventBatchRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def post_behavior_event_batch(
    payload: BehaviorEventBatchCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return record_behavior_event_batch(db, current_user, payload)
