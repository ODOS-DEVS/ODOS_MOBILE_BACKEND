import logging

from app.core.config import settings
from app.services.arkesel_service import ArkeselSmsError, generate_otp

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
