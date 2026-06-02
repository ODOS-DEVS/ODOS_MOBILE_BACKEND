import logging

logger = logging.getLogger(__name__)


def send_phone_verification_code(*, phone_number: str, code: str) -> None:
    """
    Dispatch an SMS verification code.

  Integrate an SMS provider (Hubtel, Africa's Talking, Twilio, etc.) here.
  For now we log the code so local/dev flows remain testable.
    """
    logger.info(
        "ODOS phone verification code for %s: %s",
        phone_number,
        code,
    )
