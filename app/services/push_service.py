from __future__ import annotations

import logging

import requests

from app.models import NotificationEvent, User

logger = logging.getLogger(__name__)

EXPO_PUSH_ENDPOINT = "https://exp.host/--/api/v2/push/send"


def build_push_data(
    *,
    push_type: str,
    route_type: str | None = None,
    route_target_id: str | None = None,
    notification_event: NotificationEvent | None = None,
    extra: dict | None = None,
) -> dict:
    data: dict = {"type": push_type}

    if route_type:
        data["routeType"] = route_type
    if route_target_id:
        data["routeTargetId"] = route_target_id
    if notification_event is not None:
        data["notificationId"] = str(notification_event.id)
        data["kind"] = notification_event.kind
    if extra:
        data.update(extra)

    return data


def send_expo_push_notification(
    *,
    user: User,
    title: str,
    body: str,
    data: dict | None = None,
) -> None:
    if not user.expo_push_token or not user.allow_notifications:
        return

    payload = {
        "to": user.expo_push_token,
        "title": title,
        "body": body,
        "data": data or {},
        "sound": "default",
        "priority": "high",
        "channelId": "default",
    }

    response = requests.post(
        EXPO_PUSH_ENDPOINT,
        headers={
            "accept": "application/json",
            "content-type": "application/json",
        },
        json=payload,
        timeout=15,
    )

    if response.status_code >= 400:
        logger.warning(
            "Expo push send failed for user %s with status %s: %s",
            user.id,
            response.status_code,
            response.text,
        )
