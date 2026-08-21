"""USSD (Unstructured Supplementary Service Data) payment provider."""

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


class USSDProvider(PaymentProvider):
    """USSD payment provider for basic phone-based transactions."""

    def __init__(self, api_key: str):
        """Initialize USSD provider with API key."""
        self.provider_type = PaymentProviderType.USSD
        self.api_key = api_key
        self.base_url = "https://api.ussd.com.gh/v1"  # Example USSD provider
        self.timeout = 30

    async def initiate_payment(
        self, request: PaymentInitiationRequest
    ) -> PaymentInitiationResponse:
        """Initiate USSD payment."""
        if not request.phone_number:
            return PaymentInitiationResponse(
                provider=self.provider_type,
                success=False,
                message="Phone number is required for USSD payments",
            )

        # Normalize phone number
        phone = request.phone_number.replace("+", "").replace(" ", "").strip()
        if phone.startswith("0"):
            phone = "233" + phone[1:]
        elif not phone.startswith("233"):
            phone = "233" + phone

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                payload = {
                    "amount": str(request.amount_subunit / 100),  # Convert to GHS
                    "currency": request.currency,
                    "phone": phone,
                    "reference": request.order_id,
                    "description": "ODOS Marketplace Order",
                }

                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }

                response = await client.post(
                    f"{self.base_url}/initiate",
                    json=payload,
                    headers=headers,
                )

                if response.status_code in [200, 201]:
                    result = response.json()
                    ussd_code = result.get("ussd_code", "*123*1*1*1#")
                    return PaymentInitiationResponse(
                        provider=self.provider_type,
                        success=True,
                        message=f"Dial {ussd_code} to complete payment",
                        metadata={
                            "ussd_code": ussd_code,
                            "phone_number": phone,
                            "session_id": result.get("session_id"),
                        },
                        next_action="dial_ussd",
                    )
                else:
                    return PaymentInitiationResponse(
                        provider=self.provider_type,
                        success=False,
                        message="Failed to generate USSD code",
                    )

        except httpx.TimeoutException:
            return PaymentInitiationResponse(
                provider=self.provider_type,
                success=False,
                message="USSD service timeout. Please try again.",
            )
        except Exception as e:
            return PaymentInitiationResponse(
                provider=self.provider_type,
                success=False,
                message=f"Error initiating USSD payment: {str(e)}",
            )

    async def verify_payment(
        self, request: PaymentVerificationRequest
    ) -> PaymentVerificationResponse:
        """Verify USSD payment status."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }

                response = await client.get(
                    f"{self.base_url}/status/{request.order_id}",
                    headers=headers,
                )

                if response.status_code == 200:
                    result = response.json()
                    status = result.get("status", "").lower()

                    if status == "completed":
                        payment_status = "success"
                    elif status == "pending":
                        payment_status = "pending"
                    elif status in ["failed", "expired", "cancelled"]:
                        payment_status = "failed"
                    else:
                        payment_status = "pending"

                    return PaymentVerificationResponse(
                        provider=self.provider_type,
                        order_id=request.order_id,
                        status=payment_status,
                        transaction_id=result.get("transaction_id"),
                        amount_subunit=int(float(result.get("amount", 0)) * 100),
                        gateway_response=result.get("message"),
                        raw_response=result,
                    )
                else:
                    return PaymentVerificationResponse(
                        provider=self.provider_type,
                        order_id=request.order_id,
                        status="pending",
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
        return [
            {
                "id": "ussd",
                "name": "USSD Payment",
                "type": "ussd",
                "description": "Dial USSD code to pay (works on all phones)",
                "icon": "ussd",
                "requires_phone": True,
                "fees_percent": 0,
                "min_amount": 100,  # 1 GHS
                "max_amount": 100000,  # 1000 GHS
            }
        ]
