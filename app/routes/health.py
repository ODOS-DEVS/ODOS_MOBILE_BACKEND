import hashlib
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.cache import cache_is_enabled
from app.core.config import settings, settings_secret_is_live
from app.core.database import get_db
from app.core.redis_client import get_redis, redis_is_enabled, redis_last_error

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


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
            # Logged for operators, not returned — a connection-error string
            # can carry hostnames/credentials fragments and this endpoint is
            # intentionally unauthenticated (used for deploy/uptime checks).
            error = redis_last_error()
            if error:
                logger.warning("Redis health check failed: %s", error[:300])

    if not settings.cache_enabled:
        payload["cache"] = "disabled"

    try:
        db.execute(text("SELECT 1"))
        payload["database"] = "connected"
    except Exception as exc:
        payload["database"] = "unavailable"
        logger.warning("Database health check failed: %s", str(exc)[:300])

    return payload


def _secret_fingerprint(secret: str) -> str | None:
    """A short, non-reversible tag for a credential.

    Exists to answer one question after a deploy -- "is this environment on the
    live key or still on the test one?" -- without placing a real order to find
    out, and without putting any part of the key on an unauthenticated
    endpoint. Compare it against the same digest taken locally:

        printf %s "$KUTANAPAY_API_KEY" | shasum -a 256 | cut -c1-8

    Truncated to 8 hex characters. That is deliberately too short to attack and
    long enough to tell two keys apart.
    """
    cleaned = (secret or "").strip()
    if not cleaned:
        return None
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:8]


@router.get("/health/payments")
def payments_health_check():
    """Is this environment actually able to take and confirm a payment?

    Three things fail silently and this names all three. A missing API key
    makes checkout answer 503 and reads to a customer as "temporarily
    unavailable". A missing webhook secret still lets checkouts be created --
    `kutanapay_is_configured` does not require it -- but every callback is then
    rejected as unsigned, so money arrives and no order is ever activated. And
    a key left on the test value takes test money in production.

    No secret or any part of one is returned; see _secret_fingerprint.
    """
    return {
        "collections_provider": "kutanapay",
        "kutanapay": {
            "api_key_set": bool(settings.kutanapay_api_key.strip()),
            "api_key_fingerprint": _secret_fingerprint(settings.kutanapay_api_key),
            # Checkout creation does not need this; webhook verification does.
            # Without it a paid order stays pending until someone reopens the app.
            "webhook_secret_set": bool((settings.kutanapay_webhook_secret or "").strip()),
            "webhook_secret_fingerprint": _secret_fingerprint(
                settings.kutanapay_webhook_secret
            ),
            "base_url": settings.kutanapay_base_url,
            "currency": settings.kutanapay_currency,
            "ready_to_collect": settings.kutanapay_is_configured,
            "ready_to_confirm": bool((settings.kutanapay_webhook_secret or "").strip()),
        },
        # Still live for wallet top-ups and every vendor payout, so its mode
        # matters even though it no longer collects order payments.
        "paystack": {
            "secret_key_set": bool(settings.paystack_secret_key.strip()),
            "mode": _paystack_mode(settings.paystack_secret_key),
            "webhook_secret_set": bool(settings.paystack_webhook_secret.strip()),
            "payouts_enabled": settings.paystack_payouts_enabled,
            "currency": settings.paystack_currency,
        },
        # False while Paystack is on a test key, because a test-mode top-up
        # credits a real spendable balance. See wallet_topups_are_allowed.
        "wallet_topups_allowed": settings.wallet_topups_are_allowed,
    }


def _paystack_mode(secret_key: str) -> str:
    """Paystack states its mode in the key prefix, so report it directly.

    KutanaPay's keys carry no such marker, which is why that side of the
    response above reports a fingerprint instead. "live" defers to
    settings_secret_is_live so this display and the top-up gate can never
    disagree about what counts as live.
    """
    cleaned = (secret_key or "").strip()
    if not cleaned:
        return "unconfigured"
    if settings_secret_is_live(cleaned):
        return "live"
    if cleaned.startswith("sk_test_"):
        return "test"
    return "unrecognised"


@router.get("/db-health")
def database_health_check(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"database": "connected"}
