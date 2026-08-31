from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.controllers.review_controller import (
    list_product_reviews,
    list_user_reviews,
    upsert_review,
)
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models import User
from app.schemas.review import ProductReviewRead, ReviewUpsert, UserReviewRead

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/products/{product_id}", response_model=list[ProductReviewRead])
def get_product_reviews(
    product_id: str,
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return list_product_reviews(db, product_id, limit=limit)


@router.get("/me", response_model=list[UserReviewRead])
def get_my_reviews(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return list_user_reviews(db, current_user)


@router.post("", response_model=UserReviewRead, status_code=status.HTTP_201_CREATED)
def post_review(
    payload: ReviewUpsert,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
):
    return upsert_review(db, current_user, payload)
