"""Service for two-factor authentication (2FA) for sensitive operations."""

import random
import string
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional
import logging

logger = logging.getLogger(__name__)


class TwoFactorService:
    """Handle 2FA operations like OTP generation and verification."""

    # In-memory OTP store (in production, use Redis)
    _otp_store: dict = {}

    OTP_LENGTH = 6
    OTP_VALIDITY_MINUTES = 5
    MAX_OTP_ATTEMPTS = 5

    @classmethod
    def generate_otp(cls, user_id: str) -> str:
        """
        Generate a new OTP for the user.

        Args:
            user_id: The user requesting 2FA

        Returns:
            Generated OTP code
        """
        # Generate random 6-digit OTP
        otp = "".join(random.choices(string.digits, k=cls.OTP_LENGTH))

        # Store OTP with expiry and attempt count
        now = datetime.now(timezone.utc)
        expiry = now + timedelta(minutes=cls.OTP_VALIDITY_MINUTES)

        cls._otp_store[user_id] = {
            "otp": otp,
            "expiry": expiry,
            "attempts": 0,
            "created_at": now,
        }

        logger.info(f"Generated OTP for user {user_id}")
        return otp

    @classmethod
    def verify_otp(cls, user_id: str, provided_otp: str) -> Tuple[bool, str]:
        """
        Verify that the provided OTP is correct.

        Args:
            user_id: The user verifying OTP
            provided_otp: The OTP code provided by user

        Returns:
            (is_valid, message)
        """
        if user_id not in cls._otp_store:
            return False, "No OTP request found. Please request a new OTP."

        otp_data = cls._otp_store[user_id]
        now = datetime.now(timezone.utc)

        # Check if OTP has expired
        if now > otp_data["expiry"]:
            del cls._otp_store[user_id]
            return False, "OTP has expired. Please request a new one."

        # Check attempt count
        if otp_data["attempts"] >= cls.MAX_OTP_ATTEMPTS:
            del cls._otp_store[user_id]
            return False, f"Too many failed attempts. Please request a new OTP."

        # Increment attempt count
        otp_data["attempts"] += 1

        # Check if OTP matches
        if provided_otp.strip() != otp_data["otp"]:
            remaining = cls.MAX_OTP_ATTEMPTS - otp_data["attempts"]
            return False, f"Invalid OTP. {remaining} attempts remaining."

        # OTP is valid, clean up
        del cls._otp_store[user_id]
        logger.info(f"OTP verified successfully for user {user_id}")
        return True, "OTP verified successfully"

    @classmethod
    def is_otp_pending(cls, user_id: str) -> bool:
        """Check if user has a pending OTP."""
        if user_id not in cls._otp_store:
            return False

        otp_data = cls._otp_store[user_id]
        now = datetime.now(timezone.utc)

        # Check if expired
        if now > otp_data["expiry"]:
            del cls._otp_store[user_id]
            return False

        return True

    @classmethod
    def get_otp_info(cls, user_id: str) -> Optional[dict]:
        """Get information about pending OTP (for debugging)."""
        if user_id not in cls._otp_store:
            return None

        otp_data = cls._otp_store[user_id]
        now = datetime.now(timezone.utc)

        return {
            "pending": True,
            "created_at": otp_data["created_at"],
            "expires_at": otp_data["expiry"],
            "expires_in_seconds": (otp_data["expiry"] - now).total_seconds(),
            "attempts_used": otp_data["attempts"],
            "attempts_remaining": cls.MAX_OTP_ATTEMPTS - otp_data["attempts"],
        }

    @classmethod
    def invalidate_otp(cls, user_id: str):
        """Invalidate any pending OTP for the user."""
        if user_id in cls._otp_store:
            del cls._otp_store[user_id]
            logger.info(f"Invalidated OTP for user {user_id}")

    @classmethod
    def cleanup_expired_otps(cls):
        """Remove all expired OTPs from store."""
        now = datetime.now(timezone.utc)
        expired_users = [
            user_id for user_id, data in cls._otp_store.items()
            if now > data["expiry"]
        ]

        for user_id in expired_users:
            del cls._otp_store[user_id]

        if expired_users:
            logger.info(f"Cleaned up {len(expired_users)} expired OTPs")


class BackupCodeService:
    """Generate and manage backup codes for 2FA."""

    BACKUP_CODE_LENGTH = 8
    BACKUP_CODES_COUNT = 10

    @classmethod
    def generate_backup_codes(cls) -> list[str]:
        """
        Generate backup codes for 2FA recovery.

        Returns:
            List of backup codes
        """
        codes = [
            "".join(random.choices(string.ascii_uppercase + string.digits, k=cls.BACKUP_CODE_LENGTH))
            for _ in range(cls.BACKUP_CODES_COUNT)
        ]
        return codes

    @classmethod
    def format_backup_codes(cls, codes: list[str]) -> str:
        """Format backup codes for display/printing."""
        formatted = "\n".join([f"{i+1:2d}. {code}" for i, code in enumerate(codes)])
        return formatted
