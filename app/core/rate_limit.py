import hashlib
import logging
import re
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core.redis_client import get_redis, redis_is_enabled
from app.models import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitRule:
    scope: str
    identifier: str
    limit: int
    window_seconds: int


def client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def _redis_key(scope: str, identifier: str) -> str:
    normalized = identifier.strip().lower()
    if len(normalized) > 160:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
        normalized = digest
    safe = re.sub(r"[^a-z0-9@._:-]", "_", normalized)
    return f"rl:{scope}:{safe}"


def enforce_rate_limits(rules: list[RateLimitRule]) -> None:
    if not rules or not redis_is_enabled():
        return

    client = get_redis()
    if client is None:
        return

    try:
        for rule in rules:
            if rule.limit <= 0 or rule.window_seconds <= 0:
                continue

            key = _redis_key(rule.scope, rule.identifier)
            count = client.incr(key)
            if count == 1:
                client.expire(key, rule.window_seconds)

            if count > rule.limit:
                retry_after = client.ttl(key)
                if retry_after is None or retry_after < 1:
                    retry_after = rule.window_seconds

                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        "Too many requests. Please wait before trying again."
                    ),
                    headers={"Retry-After": str(retry_after)},
                )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Rate limit skipped due to Redis error: %s", exc)


def limit_signup(request: Request) -> None:
    enforce_rate_limits(
        [
            RateLimitRule(
                scope="auth:signup:ip",
                identifier=client_ip(request),
                limit=3,
                window_seconds=3600,
            )
        ]
    )


def limit_login(request: Request, email: str) -> None:
    ip = client_ip(request)
    email_key = email.strip().lower()
    enforce_rate_limits(
        [
            RateLimitRule("auth:login:ip", ip, 10, 900),
            RateLimitRule("auth:login:email", email_key, 10, 900),
        ]
    )


def limit_google_auth(request: Request) -> None:
    enforce_rate_limits(
        [
            RateLimitRule(
                scope="auth:google:ip",
                identifier=client_ip(request),
                limit=20,
                window_seconds=900,
            )
        ]
    )


def limit_forgot_password(request: Request, email: str) -> None:
    ip = client_ip(request)
    email_key = email.strip().lower()
    enforce_rate_limits(
        [
            RateLimitRule("auth:forgot-password:ip", ip, 5, 3600),
            RateLimitRule("auth:forgot-password:email", email_key, 3, 3600),
        ]
    )


def limit_verify_reset_code(request: Request, email: str) -> None:
    ip = client_ip(request)
    email_key = email.strip().lower()
    enforce_rate_limits(
        [
            RateLimitRule("auth:verify-reset:ip", ip, 10, 900),
            RateLimitRule("auth:verify-reset:email", email_key, 5, 900),
        ]
    )


def limit_reset_password(request: Request) -> None:
    enforce_rate_limits(
        [
            RateLimitRule(
                scope="auth:reset-password:ip",
                identifier=client_ip(request),
                limit=10,
                window_seconds=3600,
            )
        ]
    )


def limit_verify_email(user: User) -> None:
    enforce_rate_limits(
        [
            RateLimitRule(
                scope="auth:verify-email:user",
                identifier=str(user.id),
                limit=5,
                window_seconds=900,
            )
        ]
    )


def limit_resend_email_verification(user: User) -> None:
    user_id = str(user.id)
    enforce_rate_limits(
        [
            RateLimitRule("auth:resend-email:user:minute", user_id, 1, 60),
            RateLimitRule("auth:resend-email:user:day", user_id, 5, 86400),
        ]
    )


def limit_send_phone_code(user: User, phone_number: str) -> None:
    user_id = str(user.id)
    phone_key = phone_number.strip()
    enforce_rate_limits(
        [
            RateLimitRule("auth:phone-send:user:minute", user_id, 3, 60),
            RateLimitRule("auth:phone-send:user:day", user_id, 5, 86400),
            RateLimitRule("auth:phone-send:phone:day", phone_key, 5, 86400),
        ]
    )


def limit_verify_phone(user: User) -> None:
    enforce_rate_limits(
        [
            RateLimitRule(
                scope="auth:verify-phone:user",
                identifier=str(user.id),
                limit=5,
                window_seconds=900,
            )
        ]
    )


def limit_payment_checkout(user: User) -> None:
    enforce_rate_limits(
        [
            RateLimitRule(
                scope="payments:checkout:user",
                identifier=str(user.id),
                limit=10,
                window_seconds=3600,
            )
        ]
    )


def limit_wallet_topup_checkout(user: User) -> None:
    enforce_rate_limits(
        [
            RateLimitRule(
                scope="wallet:topup-checkout:user",
                identifier=str(user.id),
                limit=10,
                window_seconds=3600,
            )
        ]
    )


def limit_wallet_checkout(user: User) -> None:
    enforce_rate_limits(
        [
            RateLimitRule(
                scope="wallet:checkout:user",
                identifier=str(user.id),
                limit=20,
                window_seconds=3600,
            )
        ]
    )


def limit_assistant_chat(request: Request, user: User | None) -> None:
    identifier = str(user.id) if user else client_ip(request)
    scope = "assistant:chat:user" if user else "assistant:chat:ip"
    enforce_rate_limits(
        [
            RateLimitRule(
                scope=scope,
                identifier=identifier,
                limit=40 if user else 20,
                window_seconds=3600,
            )
        ]
    )
