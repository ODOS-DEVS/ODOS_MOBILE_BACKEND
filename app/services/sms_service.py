import logging

from app.core.config import settings
from app.services.arkesel_service import ArkeselSmsError, generate_otp, send_sms

logger = logging.getLogger(__name__)


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
    total_amount: float,
    payment_label: str | None = None,
) -> None:
    payment_text = (payment_label or "your payment method").strip()
    message = (
        f"ODOS: Payment confirmed for order #{order_number}. "
        f"Total GHS {total_amount:.2f} via {payment_text}. "
        "Track your order in the app."
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
