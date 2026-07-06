from __future__ import annotations

import logging

from app.core.config import settings
from app.services.arkesel_service import ArkeselSmsError, generate_otp, send_sms

logger = logging.getLogger(__name__)


def _format_ghs(amount: float) -> str:
    return f"GHS {amount:.2f}"


def build_order_payment_confirmation_message(
    *,
    order_number: str,
    subtotal_amount: float,
    shipping_amount: float,
    discount_amount: float,
    total_amount: float,
    payment_label: str | None = None,
    payment_type: str | None = None,
    wallet_balance_after: float | None = None,
) -> str:
    payment_text = (payment_label or "your payment method").strip()
    payment_kind = (payment_type or "").strip().lower()
    via_wallet = payment_kind == "wallet" or "wallet" in payment_text.lower()

    lines = [f"ODOS: Order #{order_number} payment confirmed."]
    lines.append(f"Products: {_format_ghs(subtotal_amount)}")
    lines.append(f"Delivery: {_format_ghs(shipping_amount)}")
    if discount_amount > 0:
        lines.append(f"Voucher: -{_format_ghs(discount_amount)}")
    lines.append(f"Total paid: {_format_ghs(total_amount)}")

    if via_wallet and wallet_balance_after is not None:
        lines.append(f"Wallet balance left: {_format_ghs(wallet_balance_after)}")
    else:
        lines.append(f"Paid via {payment_text}")

    lines.append("Track your order in the ODOS app.")
    return " ".join(lines)


def send_phone_verification_code(*, phone_number: str, code: str) -> None:
    """
    Dispatch an SMS verification code.

    Uses Arkesel OTP when configured; otherwise logs the code for local/dev testing.
    """
    if settings.arkesel_is_configured:
        try:
            generate_otp(phone_number=phone_number)
        except ArkeselSmsError:
            raise
        except Exception as exc:
            logger.exception("Unexpected Arkesel OTP failure for %s", phone_number)
            raise ArkeselSmsError(
                "We couldn't send a verification code right now. Try again shortly."
            ) from exc
        return

    logger.info(
        "ODOS phone verification code for %s: %s",
        phone_number,
        code,
    )


def send_order_payment_confirmation_sms(
    *,
    phone_number: str,
    order_number: str,
    subtotal_amount: float,
    shipping_amount: float,
    discount_amount: float,
    total_amount: float,
    payment_label: str | None = None,
    payment_type: str | None = None,
    wallet_balance_after: float | None = None,
) -> None:
    message = build_order_payment_confirmation_message(
        order_number=order_number,
        subtotal_amount=subtotal_amount,
        shipping_amount=shipping_amount,
        discount_amount=discount_amount,
        total_amount=total_amount,
        payment_label=payment_label,
        payment_type=payment_type,
        wallet_balance_after=wallet_balance_after,
    )

    if settings.arkesel_is_configured:
        try:
            send_sms(phone_number=phone_number, message=message)
        except ArkeselSmsError:
            raise
        except Exception as exc:
            logger.exception("Unexpected Arkesel SMS failure for %s", phone_number)
            raise ArkeselSmsError(
                "We couldn't send that text message right now. Try again shortly."
            ) from exc
        return

    logger.info("ODOS order confirmation SMS for %s: %s", phone_number, message)
