from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.redis_client import get_redis
from app.core.database import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    payload = {"status": "ok", "rate_limit": "disabled"}
    if settings.rate_limit_is_active:
        client = get_redis()
        payload["rate_limit"] = "active" if client is not None else "unavailable"
    return payload


@router.get("/db-health")
def database_health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "connected"}
