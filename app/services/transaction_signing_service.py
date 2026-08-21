"""Service for signing and verifying payment transactions."""

import hmac
import hashlib
import json
import os
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class TransactionSigningService:
    """Service for cryptographic signing and verification of transactions."""

    @classmethod
    def get_signing_key(cls) -> str:
        """Get the HMAC signing key from environment."""
        key = os.getenv("TRANSACTION_SIGNING_KEY")
        if not key:
            raise ValueError("TRANSACTION_SIGNING_KEY environment variable not set")
        return key

    @classmethod
    def sign_transaction(cls, transaction_data: Dict[str, Any]) -> str:
        """
        Create an HMAC-SHA256 signature for a transaction.

        Args:
            transaction_data: Dictionary containing transaction details

        Returns:
            Hex-encoded HMAC-SHA256 signature
        """
        key = cls.get_signing_key()

        # Create a canonical representation of the data
        canonical_data = json.dumps(transaction_data, sort_keys=True, separators=(",", ":"))

        # Generate HMAC-SHA256
        signature = hmac.new(
            key.encode(),
            canonical_data.encode(),
            hashlib.sha256
        ).hexdigest()

        return signature

    @classmethod
    def verify_transaction_signature(cls, transaction_data: Dict[str, Any], provided_signature: str) -> bool:
        """
        Verify that a transaction signature is valid.

        Args:
            transaction_data: Dictionary containing transaction details
            provided_signature: The signature to verify

        Returns:
            True if signature is valid, False otherwise
        """
        expected_signature = cls.sign_transaction(transaction_data)

        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(expected_signature, provided_signature)

    @classmethod
    def sign_webhook_payload(cls, payload: str) -> str:
        """
        Sign a webhook payload for provider verification.

        Args:
            payload: The raw webhook payload string

        Returns:
            Hex-encoded HMAC-SHA256 signature
        """
        key = cls.get_signing_key()
        signature = hmac.new(
            key.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        return signature

    @classmethod
    def verify_webhook_signature(cls, payload: str, provided_signature: str) -> bool:
        """
        Verify that a webhook payload signature is valid.

        Args:
            payload: The raw webhook payload string
            provided_signature: The signature provided by the webhook sender

        Returns:
            True if signature is valid, False otherwise
        """
        expected_signature = cls.sign_webhook_payload(payload)
        return hmac.compare_digest(expected_signature, provided_signature)

    @classmethod
    def create_idempotency_key(cls, user_id: str, amount: float, recipient: str) -> str:
        """
        Create an idempotency key to prevent duplicate transactions.

        Args:
            user_id: The user making the transaction
            amount: The transaction amount
            recipient: The recipient account

        Returns:
            Hex-encoded idempotency key
        """
        data = f"{user_id}:{amount}:{recipient}"
        key = cls.get_signing_key()

        idempotency = hmac.new(
            key.encode(),
            data.encode(),
            hashlib.sha256
        ).hexdigest()

        return idempotency

    @classmethod
    def verify_idempotency_key(cls, user_id: str, amount: float, recipient: str, provided_key: str) -> bool:
        """
        Verify that an idempotency key matches the transaction.

        Args:
            user_id: The user making the transaction
            amount: The transaction amount
            recipient: The recipient account
            provided_key: The idempotency key to verify

        Returns:
            True if key matches, False otherwise
        """
        expected_key = cls.create_idempotency_key(user_id, amount, recipient)
        return hmac.compare_digest(expected_key, provided_key)


class WebhookSignatureVerifier:
    """Verify signatures from external payment providers."""

    @classmethod
    def verify_paystack_webhook(cls, payload: str, signature: str) -> bool:
        """Verify Paystack webhook signature."""
        key = os.getenv("PAYSTACK_SECRET_KEY")
        if not key:
            logger.error("PAYSTACK_SECRET_KEY not configured")
            return False

        expected = hmac.new(
            key.encode(),
            payload.encode(),
            hashlib.sha512
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    @classmethod
    def verify_momo_webhook(cls, payload: str, signature: str) -> bool:
        """Verify MTN Momo webhook signature."""
        key = os.getenv("MTN_MOMO_API_KEY")
        if not key:
            logger.error("MTN_MOMO_API_KEY not configured")
            return False

        expected = hmac.new(
            key.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected, signature)

    @classmethod
    def verify_stripe_webhook(cls, payload: str, signature: str) -> bool:
        """Verify Stripe webhook signature (if implemented)."""
        key = os.getenv("STRIPE_WEBHOOK_SECRET")
        if not key:
            logger.error("STRIPE_WEBHOOK_SECRET not configured")
            return False

        expected = hmac.new(
            key.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected, signature)


class TokenManager:
    """Manage temporary tokens for sensitive operations."""

    @classmethod
    def create_withdrawal_token(cls, vendor_id: str, amount: float, valid_hours: int = 24) -> tuple[str, datetime]:
        """
        Create a withdrawal authorization token.

        Args:
            vendor_id: The vendor requesting withdrawal
            amount: The withdrawal amount
            valid_hours: Token validity in hours

        Returns:
            Tuple of (token, expiry_time)
        """
        data = f"{vendor_id}:{amount}:{datetime.now(timezone.utc).isoformat()}"
        key = os.getenv("TRANSACTION_SIGNING_KEY", "default-key")

        token = hmac.new(
            key.encode(),
            data.encode(),
            hashlib.sha256
        ).hexdigest()

        expiry = datetime.now(timezone.utc) + timedelta(hours=valid_hours)

        return token, expiry

    @classmethod
    def verify_withdrawal_token(cls, vendor_id: str, amount: float, token: str, created_time: datetime) -> bool:
        """
        Verify that a withdrawal token is valid and not expired.

        Args:
            vendor_id: The vendor requesting withdrawal
            amount: The withdrawal amount
            token: The token to verify
            created_time: When the token was created

        Returns:
            True if token is valid, False otherwise
        """
        # Check if token has expired (24 hour window)
        if datetime.now(timezone.utc) - created_time > timedelta(hours=24):
            return False

        # Verify token signature
        data = f"{vendor_id}:{amount}:{created_time.isoformat()}"
        key = os.getenv("TRANSACTION_SIGNING_KEY", "default-key")

        expected_token = hmac.new(
            key.encode(),
            data.encode(),
            hashlib.sha256
        ).hexdigest()

        return hmac.compare_digest(expected_token, token)
