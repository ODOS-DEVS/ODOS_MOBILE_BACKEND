"""Home feed API routes for personalized content."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_optional_current_user
from app.core.database import get_db
from app.controllers.home_feed_controller import (
    get_home_feed_structure,
    get_home_feed_full,
    get_feed_section,
)
from app.models import User

router = APIRouter(prefix="/home-feed", tags=["home-feed"])


@router.get("/")
async def get_home_feed_endpoint(
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
    full_products: bool = Query(default=False, description="Include full product details"),
    limit_per_section: int = Query(default=12, ge=1, le=30),
):
    """Get personalized home feed.

    Returns feed sections:
    - recommended: Personalized recommendations (or best-sellers for new users)
    - trending: Hot items this week
    - recently_viewed: Items you've browsed
    - wishlist: Items you've saved
    - category_spotlight: Popular category this week
    """
    if full_products:
        return await get_home_feed_full(
            db,
            current_user,
            limit_per_section=limit_per_section,
        )
    else:
        return await get_home_feed_structure(db, current_user)


@router.get("/section/{section_key}")
async def get_feed_section_endpoint(
    section_key: str,
    db: Session = Depends(get_db),
    current_user: User | None = Depends(get_optional_current_user),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Get paginated products from a specific feed section.

    Available sections:
    - recommended
    - trending
    - recently_viewed
    - wishlist
    - category_spotlight
    """
    return await get_feed_section(db, current_user, section_key, limit=limit, offset=offset)
