"""Encryption service for sensitive data like payment account numbers."""

import os
from cryptography.fernet import Fernet
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class EncryptionService:
    """Service for encrypting and decrypting sensitive financial data."""

    _cipher: Optional[Fernet] = None

    @classmethod
    def _get_cipher(cls) -> Fernet:
        """Get or initialize the Fernet cipher."""
        if cls._cipher is None:
            key = os.getenv("ENCRYPTION_KEY")
            if not key:
                raise ValueError("ENCRYPTION_KEY environment variable not set")
            cls._cipher = Fernet(key.encode() if isinstance(key, str) else key)
        return cls._cipher

    @classmethod
    def encrypt(cls, data: str) -> str:
        """Encrypt a string value."""
        if not data:
            return ""
        try:
            cipher = cls._get_cipher()
            encrypted = cipher.encrypt(data.encode())
            return encrypted.decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise

    @classmethod
    def decrypt(cls, encrypted_data: str) -> str:
        """Decrypt an encrypted string."""
        if not encrypted_data:
            return ""
        try:
            cipher = cls._get_cipher()
            decrypted = cipher.decrypt(encrypted_data.encode())
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise

    @classmethod
    def encrypt_account_number(cls, account_number: str) -> str:
        """Encrypt a payment account number."""
        if not account_number:
            return ""
        # Only encrypt the full number, keep last 4 digits for masking
        return cls.encrypt(account_number)

    @classmethod
    def decrypt_account_number(cls, encrypted_account_number: str) -> str:
        """Decrypt a payment account number."""
        if not encrypted_account_number:
            return ""
        return cls.decrypt(encrypted_account_number)

    @classmethod
    def mask_account_number(cls, account_number: str, visible_chars: int = 4) -> str:
        """Create a masked version of an account number for display."""
        if not account_number or len(account_number) <= visible_chars:
            return "*" * len(account_number)

        # Show only last N characters
        masked = "*" * (len(account_number) - visible_chars) + account_number[-visible_chars:]
        return masked

    @classmethod
    def generate_encryption_key(cls) -> str:
        """Generate a new Fernet encryption key."""
        key = Fernet.generate_key()
        return key.decode()

    @classmethod
    def rotate_encryption_key(cls, old_data: dict, new_key: str) -> dict:
        """
        Re-encrypt data with a new encryption key.

        Usage:
            old_cipher = EncryptionService._cipher
            new_data = rotate_encryption_key(data, new_key)
            EncryptionService._cipher = Fernet(new_key)
        """
        rotated_data = {}
        for key, value in old_data.items():
            if value and isinstance(value, str):
                try:
                    # Decrypt with old key, encrypt with new key
                    decrypted = cls.decrypt(value)
                    cls._cipher = Fernet(new_key.encode() if isinstance(new_key, str) else new_key)
                    rotated_data[key] = cls.encrypt(decrypted)
                except Exception as e:
                    logger.error(f"Key rotation failed for {key}: {e}")
                    rotated_data[key] = value
            else:
                rotated_data[key] = value
        return rotated_data
