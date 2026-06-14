import logging
import ssl
import time
from typing import TYPE_CHECKING

import certifi

from app.core.config import settings

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)

_redis_client: "redis.Redis | None" = None
_last_redis_error: str | None = None


def redis_is_configured() -> bool:
    return bool(settings.redis_url.strip())


def redis_is_enabled() -> bool:
    return settings.rate_limit_enabled and redis_is_configured()


def redis_should_connect() -> bool:
    if not redis_is_configured():
        return False
    if settings.rate_limit_enabled:
        return True
    return settings.cache_enabled


def _build_redis_client():
    import redis

    connection_kwargs = {
        "decode_responses": True,
        "socket_connect_timeout": 10,
        "socket_timeout": 10,
    }

    if settings.redis_url.startswith("rediss://"):
        connection_kwargs["ssl_cert_reqs"] = ssl.CERT_REQUIRED
        connection_kwargs["ssl_ca_certs"] = certifi.where()

    client = redis.Redis.from_url(settings.redis_url, **connection_kwargs)
    client.ping()
    return client


def get_redis(*, force_reconnect: bool = False):
    global _redis_client, _last_redis_error

    if not redis_should_connect():
        return None

    if _redis_client is not None and not force_reconnect:
        try:
            _redis_client.ping()
            return _redis_client
        except Exception as exc:
            logger.warning("Redis ping failed, reconnecting: %s", exc)
            _redis_client = None

    if _redis_client is not None:
        return _redis_client

    try:
        import redis  # noqa: F401
    except ImportError:
        _last_redis_error = "redis package is not installed"
        logger.warning("Rate limiting disabled: %s.", _last_redis_error)
        return None

    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            _redis_client = _build_redis_client()
            _last_redis_error = None
            logger.info("Redis connected.")
            return _redis_client
        except Exception as exc:
            _last_redis_error = str(exc)
            logger.warning(
                "Redis connection attempt %s/%s failed: %s",
                attempt,
                attempts,
                exc,
            )
            if attempt < attempts:
                time.sleep(0.5 * attempt)

    _redis_client = None
    return None


def redis_last_error() -> str | None:
    return _last_redis_error


def close_redis() -> None:
    global _redis_client, _last_redis_error

    if _redis_client is not None:
        try:
            _redis_client.close()
        except Exception:
            pass

    _redis_client = None
    _last_redis_error = None
