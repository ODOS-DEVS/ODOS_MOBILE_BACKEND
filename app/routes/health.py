from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.cache import cache_is_enabled
from app.core.config import settings
from app.core.database import get_db
from app.core.redis_client import get_redis, redis_is_enabled, redis_last_error

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    """Fast liveness probe for Render and admin warmup — must not block on Redis/DB."""
    return {
        "status": "ok",
        "rate_limit": "active" if settings.rate_limit_is_active else "disabled",
        "cache": "active" if settings.cache_is_active else "disabled",
    }


@router.get("/health/services")
def services_health_check(db: Session = Depends(get_db)):
    payload = {
        "status": "ok",
        "rate_limit": "disabled",
        "cache": "disabled",
        "database": "unknown",
    }

    if redis_is_enabled() or cache_is_enabled():
        client = get_redis(force_reconnect=False)
        if client is not None:
            if redis_is_enabled():
                payload["rate_limit"] = "active"
            if cache_is_enabled():
                payload["cache"] = "active"
        else:
            if redis_is_enabled():
                payload["rate_limit"] = "unavailable"
            if cache_is_enabled():
                payload["cache"] = "unavailable"
            error = redis_last_error()
            if error:
                if redis_is_enabled():
                    payload["rate_limit_error"] = error[:160]
                if cache_is_enabled():
                    payload["cache_error"] = error[:160]

    if not settings.cache_enabled:
        payload["cache"] = "disabled"

    try:
        db.execute(text("SELECT 1"))
        payload["database"] = "connected"
    except Exception as exc:
        payload["database"] = "unavailable"
        payload["database_error"] = str(exc)[:160]

    return payload


@router.get("/db-health")
def database_health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "connected"}
