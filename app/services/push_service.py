from __future__ import annotations

import logging

import requests

from app.models import User

logger = logging.getLogger(__name__)

EXPO_PUSH_ENDPOINT = "https://exp.host/--/api/v2/push/send"


def send_expo_push_notification(
    *,
    user: User,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    if not user.expo_push_token or not user.allow_notifications:
        return

    response = requests.post(
        EXPO_PUSH_ENDPOINT,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
        },
        json={
            "to": user.expo_push_token,
            "title": title,
            "body": body,
            "data": data or {},
            "sound": "default",
        },
        timeout=15,
    )

    if response.status_code >= 400:
        logger.warning(
            "Expo push send failed for user %s with status %s: %s",
            user.id,
            response.status_code,
            response.text,
        )
