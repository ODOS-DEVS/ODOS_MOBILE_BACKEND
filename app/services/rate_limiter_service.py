"""Service for rate limiting payment endpoints to prevent abuse."""

from datetime import datetime, timedelta, timezone
from typing import Tuple
import os
import logging

logger = logging.getLogger(__name__)


class RateLimiterService:
    """Rate limiter for payment and sensitive endpoints."""

    # In-memory rate limit store (in production, use Redis)
    _rate_limits: dict = {}

    # Configuration
    PAYMENT_INITIATION_LIMIT = 10  # per minute per user
    PAYMENT_VERIFICATION_LIMIT = 20  # per minute per user
    WITHDRAWAL_REQUEST_LIMIT = 5  # per minute per vendor
    LOGIN_ATTEMPT_LIMIT = 5  # per minute per IP
    OTP_ATTEMPT_LIMIT = 5  # per 15 minutes per user

    @classmethod
    def check_rate_limit(
        cls,
        key: str,
        limit: int,
        window_seconds: int = 60,
    ) -> Tuple[bool, int, int]:
        """
        Check if an action is within rate limit.

        Args:
            key: Unique identifier (user_id, ip, etc.)
            limit: Number of allowed requests
            window_seconds: Time window in seconds

        Returns:
            (allowed, remaining, retry_after)
        """
        now = datetime.now(timezone.utc)

        if key not in cls._rate_limits:
            cls._rate_limits[key] = []

        # Remove expired entries
        cls._rate_limits[key] = [
            timestamp for timestamp in cls._rate_limits[key]
            if (now - timestamp).total_seconds() < window_seconds
        ]

        requests_in_window = len(cls._rate_limits[key])

        if requests_in_window >= limit:
            # Calculate retry time
            oldest = cls._rate_limits[key][0]
            retry_after = int(((oldest + timedelta(seconds=window_seconds)) - now).total_seconds() + 1)
            return False, 0, retry_after

        # Add current request
        cls._rate_limits[key].append(now)

        return True, limit - requests_in_window - 1, 0

    @classmethod
    def check_payment_initiation(cls, user_id: str) -> Tuple[bool, str]:
        """Check rate limit for payment initiation."""
        allowed, remaining, retry = cls.check_rate_limit(
            f"payment_init:{user_id}",
            cls.PAYMENT_INITIATION_LIMIT,
            60
        )
        if not allowed:
            return False, f"Too many payment attempts. Try again in {retry} seconds."
        return True, f"{remaining} payment attempts remaining"

    @classmethod
    def check_payment_verification(cls, user_id: str) -> Tuple[bool, str]:
        """Check rate limit for payment verification."""
        allowed, remaining, retry = cls.check_rate_limit(
            f"payment_verify:{user_id}",
            cls.PAYMENT_VERIFICATION_LIMIT,
            60
        )
        if not allowed:
            return False, f"Too many verification attempts. Try again in {retry} seconds."
        return True, f"{remaining} verification attempts remaining"

    @classmethod
    def check_withdrawal_request(cls, vendor_id: str) -> Tuple[bool, str]:
        """Check rate limit for withdrawal requests."""
        allowed, remaining, retry = cls.check_rate_limit(
            f"withdrawal:{vendor_id}",
            cls.WITHDRAWAL_REQUEST_LIMIT,
            60
        )
        if not allowed:
            return False, f"Too many withdrawal requests. Try again in {retry} seconds."
        return True, f"{remaining} withdrawal requests remaining"

    @classmethod
    def check_login_attempt(cls, ip_address: str) -> Tuple[bool, str]:
        """Check rate limit for login attempts."""
        allowed, remaining, retry = cls.check_rate_limit(
            f"login:{ip_address}",
            cls.LOGIN_ATTEMPT_LIMIT,
            60
        )
        if not allowed:
            return False, f"Too many login attempts. Try again in {retry} seconds."
        return True, ""

    @classmethod
    def check_otp_attempt(cls, user_id: str) -> Tuple[bool, str]:
        """Check rate limit for OTP verification attempts."""
        allowed, remaining, retry = cls.check_rate_limit(
            f"otp:{user_id}",
            cls.OTP_ATTEMPT_LIMIT,
            900  # 15 minutes
        )
        if not allowed:
            return False, f"Too many OTP attempts. Try again in {retry} seconds."
        return True, f"{remaining} attempts remaining"

    @classmethod
    def reset_rate_limit(cls, key: str):
        """Reset rate limit for a key (e.g., after successful login)."""
        if key in cls._rate_limits:
            cls._rate_limits[key] = []

    @classmethod
    def get_rate_limit_status(cls, key: str) -> dict:
        """Get current rate limit status for debugging."""
        if key not in cls._rate_limits:
            return {"key": key, "requests": 0, "expires": None}

        now = datetime.now(timezone.utc)
        requests = [
            (now - ts).total_seconds() for ts in cls._rate_limits[key]
        ]

        return {
            "key": key,
            "requests": len(requests),
            "request_times_ago": sorted(requests),
            "expires_in": max(requests) + 60 if requests else None,
        }
