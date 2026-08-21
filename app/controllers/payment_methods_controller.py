"""Payment methods controller for multi-provider payment support."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import User
from app.services.payment_provider import PaymentProviderFactory, PaymentProviderType


def get_available_payment_methods(db: Session, current_user: User) -> dict[str, Any]:
    """Get list of available payment methods for the current user."""
    from app.core.auth import require_user

    require_user(current_user)

    methods = PaymentProviderFactory.get_available_payment_methods()

    return {
        "available_methods": methods,
        "payment_providers": [provider.value for provider in PaymentProviderFactory.get_all_providers().keys()],
        "total_methods": len(methods),
    }


async def initiate_payment(
    db: Session,
    current_user: User,
    order_id: str,
    provider: str,
    phone_number: str | None = None,
) -> dict[str, Any]:
    """Initiate payment through specified provider."""
    from app.core.auth import require_user
    from app.models import Order

    require_user(current_user)

    # Validate provider
    try:
        provider_type = PaymentProviderType(provider)
    except ValueError:
        return {
            "success": False,
            "message": f"Invalid payment provider: {provider}",
        }

    # Get provider
    payment_provider = PaymentProviderFactory.get_provider(provider_type)
    if not payment_provider:
        return {
            "success": False,
            "message": f"Payment provider not configured: {provider}",
        }

    # Get order
    order = db.get(Order, order_id)
    if not order:
        return {
            "success": False,
            "message": "Order not found",
        }

    if order.user_id != current_user.id:
        return {
            "success": False,
            "message": "Unauthorized",
        }

    # Initiate payment
    from app.services.payment_provider import PaymentInitiationRequest

    payment_request = PaymentInitiationRequest(
        order_id=order_id,
        user_id=str(current_user.id),
        amount_subunit=int(order.total_amount * 100),
        currency="GHS",
        phone_number=phone_number,
        email=current_user.email,
    )

    response = await payment_provider.initiate_payment(payment_request)

    return {
        "success": response.success,
        "message": response.message,
        "provider": response.provider.value,
        "authorization_url": response.authorization_url,
        "provider_reference": response.provider_reference,
        "next_action": response.next_action,
        "metadata": response.metadata,
    }


async def verify_payment_status(
    db: Session,
    current_user: User,
    order_id: str,
    provider: str,
    provider_reference: str | None = None,
) -> dict[str, Any]:
    """Verify payment status through specified provider."""
    from app.core.auth import require_user

    require_user(current_user)

    # Validate provider
    try:
        provider_type = PaymentProviderType(provider)
    except ValueError:
        return {
            "success": False,
            "message": f"Invalid payment provider: {provider}",
        }

    # Get provider
    payment_provider = PaymentProviderFactory.get_provider(provider_type)
    if not payment_provider:
        return {
            "success": False,
            "message": f"Payment provider not configured: {provider}",
        }

    # Verify payment
    from app.services.payment_provider import PaymentVerificationRequest

    verification_request = PaymentVerificationRequest(
        order_id=order_id,
        provider_reference=provider_reference,
    )

    response = await payment_provider.verify_payment(verification_request)

    return {
        "success": response.status == "success",
        "order_id": response.order_id,
        "status": response.status,
        "provider": response.provider.value,
        "transaction_id": response.transaction_id,
        "amount_subunit": response.amount_subunit,
        "processor_fee_subunit": response.processor_fee_subunit,
        "gateway_response": response.gateway_response,
    }
