"""MTN Momo payment provider integration."""

import asyncio
import os
from typing import Any, Optional

import httpx

from app.services.payment_provider import (
    PaymentInitiationRequest,
    PaymentInitiationResponse,
    PaymentProvider,
    PaymentProviderType,
    PaymentVerificationRequest,
    PaymentVerificationResponse,
)


class MomoProvider(PaymentProvider):
    """Mobile Money (Momo) payment provider for MTN, Vodafone, Airtel."""

    def __init__(self, provider_type: PaymentProviderType, api_key: str, api_secret: str):
        """Initialize Momo provider with credentials."""
        self.provider_type = provider_type
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.mtn.com/v1"  # MTN Momo API endpoint
        self.timeout = 30

    async def initiate_payment(
        self, request: PaymentInitiationRequest
    ) -> PaymentInitiationResponse:
        """Initiate Momo payment via USSD or app."""
        if not request.phone_number:
            return PaymentInitiationResponse(
                provider=self.provider_type,
                success=False,
                message="Phone number is required for Momo payments",
            )

        # Normalize phone number (remove +, spaces, etc.)
        phone = request.phone_number.replace("+", "").replace(" ", "").strip()

        # Convert to MTN format if needed (e.g., 0244XXX -> 244XXX)
        if phone.startswith("0"):
            phone = "233" + phone[1:]
        elif not phone.startswith("233"):
            phone = "233" + phone

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                # Create payment request with Momo
                payload = {
                    "amount": str(request.amount_subunit / 100),  # Convert pesewas to GHS
                    "currency": request.currency,
                    "externalId": request.order_id,
                    "payer": {
                        "partyIdType": "MSISDN",
                        "partyId": phone,
                    },
                    "payerMessage": "ODOS Order Payment",
                    "payeeNote": "Purchase from ODOS Marketplace",
                }

                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "X-Reference-Id": request.order_id,
                    "Content-Type": "application/json",
                    "X-Secret": self.api_secret,
                }

                response = await client.post(
                    f"{self.base_url}/paymentRequests",
                    json=payload,
                    headers=headers,
                )

                if response.status_code in [200, 201, 202]:
                    result = response.json()
                    return PaymentInitiationResponse(
                        provider=self.provider_type,
                        success=True,
                        message="Momo payment initiated. Please complete the transaction on your phone.",
                        provider_reference=result.get("paymentRequestId"),
                        next_action="wait_for_callback",
                        metadata={
                            "phone_number": phone,
                            "payment_request_id": result.get("paymentRequestId"),
                        },
                    )
                else:
                    return PaymentInitiationResponse(
                        provider=self.provider_type,
                        success=False,
                        message=f"Failed to initiate Momo payment: {response.text}",
                    )

        except httpx.TimeoutException:
            return PaymentInitiationResponse(
                provider=self.provider_type,
                success=False,
                message="Momo payment service timeout. Please try again.",
            )
        except Exception as e:
            return PaymentInitiationResponse(
                provider=self.provider_type,
                success=False,
                message=f"Error initiating Momo payment: {str(e)}",
            )

    async def verify_payment(
        self, request: PaymentVerificationRequest
    ) -> PaymentVerificationResponse:
        """Verify Momo payment status."""
        if not request.provider_reference:
            return PaymentVerificationResponse(
                provider=self.provider_type,
                order_id=request.order_id,
                status="failed",
                gateway_response="No payment reference provided",
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }

                response = await client.get(
                    f"{self.base_url}/paymentRequests/{request.provider_reference}",
                    headers=headers,
                )

                if response.status_code == 200:
                    result = response.json()
                    status = result.get("status", "").lower()

                    # Map Momo statuses to our standard statuses
                    if status == "successful":
                        payment_status = "success"
                    elif status == "pending":
                        payment_status = "pending"
                    elif status in ["failed", "rejected"]:
                        payment_status = "failed"
                    else:
                        payment_status = "pending"

                    return PaymentVerificationResponse(
                        provider=self.provider_type,
                        order_id=request.order_id,
                        status=payment_status,
                        transaction_id=result.get("paymentRequestId"),
                        amount_subunit=int(float(result.get("amount", 0)) * 100),
                        gateway_response=result.get("reason"),
                        raw_response=result,
                    )
                else:
                    return PaymentVerificationResponse(
                        provider=self.provider_type,
                        order_id=request.order_id,
                        status="pending",
                        gateway_response="Unable to verify payment status",
                    )

        except Exception as e:
            return PaymentVerificationResponse(
                provider=self.provider_type,
                order_id=request.order_id,
                status="pending",
                gateway_response=f"Verification error: {str(e)}",
            )

    def get_supported_payment_methods(self) -> list[dict[str, Any]]:
        """Get supported payment methods."""
        provider_name = {
            PaymentProviderType.MOMO_MTN: "MTN Mobile Money",
            PaymentProviderType.MOMO_VODAFONE: "Vodafone Cash",
            PaymentProviderType.MOMO_AIRTEL: "Airtel Money",
        }.get(self.provider_type, "Mobile Money")

        return [
            {
                "id": self.provider_type.value,
                "name": provider_name,
                "type": "mobile_money",
                "description": f"Pay with {provider_name} via USSD or app",
                "icon": "momo",
                "requires_phone": True,
                "fees_percent": 0,  # No additional fees
                "min_amount": 100,  # 1 GHS
                "max_amount": 50000,  # 500 GHS
            }
        ]
