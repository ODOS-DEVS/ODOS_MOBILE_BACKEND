from typing import Any

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.core.config import settings


def verify_google_identity_token(token: str) -> dict[str, Any]:
    if not settings.google_client_id_list:
        raise ValueError("Google auth is not configured on the backend.")

    payload = google_id_token.verify_oauth2_token(
        token,
        google_requests.Request(),
        audience=None,
    )

    if payload.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise ValueError("Invalid Google token issuer.")

    audience = payload.get("aud")
    if audience not in settings.google_client_id_list:
        raise ValueError("Google token audience does not match this app.")

    return payload
