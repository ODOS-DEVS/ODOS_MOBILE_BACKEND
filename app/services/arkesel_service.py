import logging

import httpx

from app.core.config import settings
from app.core.phone import to_international_ghana_phone

logger = logging.getLogger(__name__)

ARKESEL_OTP_GENERATE_URL = "https://sms.arkesel.com/api/otp/generate"
ARKESEL_OTP_VERIFY_URL = "https://sms.arkesel.com/api/otp/verify"


class ArkeselSmsError(Exception):
    def __init__(self, message: str, *, status_code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _arkesel_headers() -> dict[str, str]:
    return {
        "api-key": settings.arkesel_api_key.strip(),
        "Content-Type": "application/json",
    }


def generate_otp(*, phone_number: str) -> None:
    international_number = to_international_ghana_phone(phone_number)
    payload = {
        "expiry": settings.phone_verification_code_expire_minutes,
        "length": 6,
        "medium": "sms",
        "type": "numeric",
        "message": settings.arkesel_otp_message,
        "number": international_number,
        "sender_id": settings.arkesel_sender_id.strip(),
    }

    try:
        response = httpx.post(
            ARKESEL_OTP_GENERATE_URL,
            headers=_arkesel_headers(),
            json=payload,
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        logger.exception("Arkesel OTP generate request failed")
        raise ArkeselSmsError(
            "We couldn't send a verification code right now. Try again shortly."
        ) from exc

    data = response.json() if response.content else {}
    status = str(data.get("status", "")).lower()
    code = str(data.get("code", ""))
    is_success = (
        response.status_code < 400
        and status != "error"
        and (code in {"1000", "1100"} or status == "success")
    )
    if not is_success:
        message = data.get("message") or "Failed to send verification code."
        logger.error(
            "Arkesel OTP generate failed for %s: http=%s status=%s code=%s message=%s body=%s",
            international_number,
            response.status_code,
            status,
            code,
            message,
            data,
        )
        raise ArkeselSmsError(message, status_code=code or None)


def verify_otp(*, phone_number: str, code: str) -> None:
    international_number = to_international_ghana_phone(phone_number)
    payload = {
        "code": code.strip(),
        "number": international_number,
    }

    try:
        response = httpx.post(
            ARKESEL_OTP_VERIFY_URL,
            headers=_arkesel_headers(),
            json=payload,
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        logger.exception("Arkesel OTP verify request failed")
        raise ArkeselSmsError(
            "We couldn't verify that code right now. Try again shortly."
        ) from exc

    data = response.json() if response.content else {}
    status = str(data.get("status", "")).lower()
    status_code = str(data.get("code", ""))
    is_success = (
        response.status_code < 400
        and status != "error"
        and (status_code == "1100" or status == "success")
    )
    if not is_success:
        message = data.get("message") or "That verification code is not correct."
        logger.warning(
            "Arkesel OTP verify rejected for %s: status=%s code=%s message=%s",
            international_number,
            response.status_code,
            status_code,
            message,
        )
        raise ArkeselSmsError(message, status_code=status_code or None)
