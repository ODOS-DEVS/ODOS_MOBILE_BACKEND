"""Bank transfer payment provider."""

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


class BankTransferProvider(PaymentProvider):
    """Bank transfer payment provider for direct account transfers."""

    def __init__(self, api_key: str, merchant_account: str = None):
        """Initialize bank transfer provider."""
        self.provider_type = PaymentProviderType.BANK_TRANSFER
        self.api_key = api_key
        self.merchant_account = merchant_account or "0123456789"  # Default merchant account
        self.base_url = "https://api.banktransfer.com.gh/v1"
        self.timeout = 30

    async def initiate_payment(
        self, request: PaymentInitiationRequest
    ) -> PaymentInitiationResponse:
        """Initiate bank transfer payment (returns bank details for user to transfer to)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                payload = {
                    "amount": str(request.amount_subunit / 100),
                    "currency": request.currency,
                    "reference": request.order_id,
                    "description": f"ODOS Order {request.order_id}",
                }

                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }

                response = await client.post(
                    f"{self.base_url}/initiate-transfer",
                    json=payload,
                    headers=headers,
                )

                if response.status_code in [200, 201]:
                    result = response.json()
                    return PaymentInitiationResponse(
                        provider=self.provider_type,
                        success=True,
                        message="Bank transfer details generated. Please transfer to the account below.",
                        metadata={
                            "account_name": "ODOS Marketplace Ghana Ltd",
                            "account_number": result.get("account_number", "0123456789"),
                            "bank_name": result.get("bank_name", "Ghana Commercial Bank"),
                            "bank_code": result.get("bank_code", "030"),
                            "amount": str(request.amount_subunit / 100),
                            "reference": request.order_id,
                            "transfer_session_id": result.get("session_id"),
                        },
                        next_action="bank_transfer",
                    )
                else:
                    return PaymentInitiationResponse(
                        provider=self.provider_type,
                        success=False,
                        message="Failed to generate bank transfer details",
                    )

        except httpx.TimeoutException:
            return PaymentInitiationResponse(
                provider=self.provider_type,
                success=False,
                message="Bank transfer service timeout",
            )
        except Exception as e:
            return PaymentInitiationResponse(
                provider=self.provider_type,
                success=False,
                message=f"Error generating bank transfer details: {str(e)}",
            )

    async def verify_payment(
        self, request: PaymentVerificationRequest
    ) -> PaymentVerificationResponse:
        """Verify bank transfer payment status."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }

                response = await client.get(
                    f"{self.base_url}/transfers/{request.order_id}/status",
                    headers=headers,
                )

                if response.status_code == 200:
                    result = response.json()
                    status = result.get("status", "").lower()

                    if status == "completed":
                        payment_status = "success"
                    elif status == "pending":
                        payment_status = "pending"
                    elif status in ["failed", "cancelled", "declined"]:
                        payment_status = "failed"
                    else:
                        payment_status = "pending"

                    return PaymentVerificationResponse(
                        provider=self.provider_type,
                        order_id=request.order_id,
                        status=payment_status,
                        transaction_id=result.get("transaction_id"),
                        amount_subunit=int(float(result.get("amount", 0)) * 100),
                        processor_fee_subunit=int(float(result.get("bank_fee", 0)) * 100),
                        gateway_response=result.get("message"),
                        raw_response=result,
                    )
                else:
                    return PaymentVerificationResponse(
                        provider=self.provider_type,
                        order_id=request.order_id,
                        status="pending",
                        gateway_response="Unable to verify bank transfer status",
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
                "id": "bank_transfer",
                "name": "Bank Transfer",
                "type": "bank_transfer",
                "description": "Transfer directly from your bank account",
                "icon": "bank",
                "requires_phone": False,
                "fees_percent": 0,
                "min_amount": 500,  # 5 GHS
                "max_amount": 500000,  # 5000 GHS (higher limit for bank transfers)
                "processing_time": "1-2 business days",
            }
        ]
