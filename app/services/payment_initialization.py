"""Initialize payment providers based on environment configuration."""

import os
from typing import Optional

from app.services.bank_transfer_provider import BankTransferProvider
from app.services.momo_provider import MomoProvider
from app.services.payment_provider import PaymentProviderFactory, PaymentProviderType
from app.services.ussd_provider import USSDProvider


def initialize_payment_providers() -> None:
    """Initialize all configured payment providers."""

    # Paystack configuration
    paystack_api_key = os.getenv("PAYSTACK_PUBLIC_KEY")
    if paystack_api_key:
        try:
            from app.services.paystack_service import PaystackProvider

            paystack_provider = PaystackProvider(paystack_api_key)
            PaymentProviderFactory.register_provider(paystack_provider)
        except ImportError:
            pass

    # MTN Momo configuration
    mtn_momo_key = os.getenv("MTN_MOMO_API_KEY")
    mtn_momo_secret = os.getenv("MTN_MOMO_API_SECRET")
    if mtn_momo_key and mtn_momo_secret:
        mtn_provider = MomoProvider(
            PaymentProviderType.MOMO_MTN,
            api_key=mtn_momo_key,
            api_secret=mtn_momo_secret,
        )
        PaymentProviderFactory.register_provider(mtn_provider)

    # Vodafone Cash configuration
    vodafone_momo_key = os.getenv("VODAFONE_CASH_API_KEY")
    vodafone_momo_secret = os.getenv("VODAFONE_CASH_API_SECRET")
    if vodafone_momo_key and vodafone_momo_secret:
        vodafone_provider = MomoProvider(
            PaymentProviderType.MOMO_VODAFONE,
            api_key=vodafone_momo_key,
            api_secret=vodafone_momo_secret,
        )
        PaymentProviderFactory.register_provider(vodafone_provider)

    # Airtel Money configuration
    airtel_momo_key = os.getenv("AIRTEL_MONEY_API_KEY")
    airtel_momo_secret = os.getenv("AIRTEL_MONEY_API_SECRET")
    if airtel_momo_key and airtel_momo_secret:
        airtel_provider = MomoProvider(
            PaymentProviderType.MOMO_AIRTEL,
            api_key=airtel_momo_key,
            api_secret=airtel_momo_secret,
        )
        PaymentProviderFactory.register_provider(airtel_provider)

    # USSD configuration
    ussd_api_key = os.getenv("USSD_API_KEY")
    if ussd_api_key:
        ussd_provider = USSDProvider(api_key=ussd_api_key)
        PaymentProviderFactory.register_provider(ussd_provider)

    # Bank Transfer configuration
    bank_api_key = os.getenv("BANK_TRANSFER_API_KEY")
    if bank_api_key:
        bank_provider = BankTransferProvider(
            api_key=bank_api_key,
            merchant_account=os.getenv("BANK_MERCHANT_ACCOUNT"),
        )
        PaymentProviderFactory.register_provider(bank_provider)


def get_configured_payment_providers() -> list[str]:
    """Get list of configured payment providers."""
    return [provider.value for provider in PaymentProviderFactory.get_all_providers().keys()]
