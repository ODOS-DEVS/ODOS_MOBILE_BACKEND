from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.controllers.auth_controller import signup_user
from app.core.database import get_db
from app.schemas.user import UserCreate, UserRead

router = APIRouter(tags=["auth"])


@router.post(
    "/signup",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
)
def signup(user_data: UserCreate, db: Session = Depends(get_db)):
    return signup_user(db, user_data)
