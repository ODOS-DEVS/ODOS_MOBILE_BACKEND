from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.redis_client import get_redis, redis_is_enabled, redis_last_error

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    payload = {"status": "ok", "rate_limit": "disabled"}
    if redis_is_enabled():
        client = get_redis(force_reconnect=False)
        if client is not None:
            payload["rate_limit"] = "active"
        else:
            payload["rate_limit"] = "unavailable"
            error = redis_last_error()
            if error:
                payload["rate_limit_error"] = error[:160]
    return payload


@router.get("/db-health")
def database_health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "connected"}
