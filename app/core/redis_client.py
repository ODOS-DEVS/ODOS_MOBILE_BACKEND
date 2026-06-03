import logging
from typing import TYPE_CHECKING

from app.core.config import settings

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

_redis_client: "redis.Redis | None" = None
_redis_checked = False


def redis_is_enabled() -> bool:
    return settings.rate_limit_enabled and bool(settings.redis_url.strip())


def get_redis():
    global _redis_client, _redis_checked

    if not redis_is_enabled():
        return None

    if _redis_client is not None:
        return _redis_client

    if _redis_checked:
        return None

    _redis_checked = True

    try:
        import redis
    except ImportError:
        logger.warning("Rate limiting disabled: redis package is not installed.")
        return None

    try:
        client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _redis_client = client
        logger.info("Redis connected for rate limiting.")
    except Exception as exc:
        logger.warning("Rate limiting disabled: Redis unavailable (%s).", exc)
        _redis_client = None

    return _redis_client


def close_redis() -> None:
    global _redis_client, _redis_checked

    if _redis_client is not None:
        try:
            _redis_client.close()
        except Exception:
            pass

    _redis_client = None
    _redis_checked = False
