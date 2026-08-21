"""Payment provider abstraction for multiple payment methods."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class PaymentProviderType(str, Enum):
    """Supported payment providers."""

    PAYSTACK = "paystack"
    MOMO_MTN = "momo_mtn"
    MOMO_VODAFONE = "momo_vodafone"
    MOMO_AIRTEL = "momo_airtel"
    BANK_TRANSFER = "bank_transfer"
    USSD = "ussd"


class PaymentMethodType(str, Enum):
    """High-level payment method categories."""

    CARD = "card"
    MOBILE_MONEY = "mobile_money"
    BANK_TRANSFER = "bank_transfer"
    USSD = "ussd"


@dataclass(slots=True)
class PaymentInitiationRequest:
    """Request to initiate a payment."""

    order_id: str
    user_id: str
    amount_subunit: int  # Amount in pesewas (1 GHS = 100 pesewas)
    currency: str = "GHS"
    phone_number: Optional[str] = None
    email: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class PaymentInitiationResponse:
    """Response from payment initiation."""

    provider: PaymentProviderType
    success: bool
    message: str
    authorization_url: Optional[str] = None
    provider_reference: Optional[str] = None
    next_action: Optional[str] = None  # "redirect", "submit_otp", "wait_for_callback", etc.
    metadata: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class PaymentVerificationRequest:
    """Request to verify payment status."""

    order_id: str
    provider_reference: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


@dataclass(slots=True)
class PaymentVerificationResponse:
    """Response from payment verification."""

    provider: PaymentProviderType
    order_id: str
    status: str  # "pending", "success", "failed", "cancelled"
    amount_subunit: Optional[int] = None
    transaction_id: Optional[str] = None
    gateway_response: Optional[str] = None
    processor_fee_subunit: Optional[int] = None
    authorization_data: Optional[dict[str, Any]] = None
    raw_response: Optional[dict[str, Any]] = None


class PaymentProvider(ABC):
    """Base class for payment providers."""

    provider_type: PaymentProviderType

    @abstractmethod
    async def initiate_payment(self, request: PaymentInitiationRequest) -> PaymentInitiationResponse:
        """Initiate a payment transaction."""
        pass

    @abstractmethod
    async def verify_payment(self, request: PaymentVerificationRequest) -> PaymentVerificationResponse:
        """Verify payment status."""
        pass

    @abstractmethod
    def get_supported_payment_methods(self) -> list[dict[str, Any]]:
        """Get list of payment methods this provider supports."""
        pass


class PaymentProviderFactory:
    """Factory for creating payment provider instances."""

    _providers: dict[PaymentProviderType, PaymentProvider] = {}

    @classmethod
    def register_provider(cls, provider: PaymentProvider) -> None:
        """Register a payment provider."""
        cls._providers[provider.provider_type] = provider

    @classmethod
    def get_provider(cls, provider_type: PaymentProviderType) -> Optional[PaymentProvider]:
        """Get a registered payment provider."""
        return cls._providers.get(provider_type)

    @classmethod
    def get_all_providers(cls) -> dict[PaymentProviderType, PaymentProvider]:
        """Get all registered providers."""
        return cls._providers.copy()

    @classmethod
    def get_available_payment_methods(cls) -> list[dict[str, Any]]:
        """Get all available payment methods from all providers."""
        methods = []
        for provider in cls._providers.values():
            methods.extend(provider.get_supported_payment_methods())
        return methods
