from __future__ import annotations

from fastapi import Request

from app.core.rate_limit import client_ip


def request_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    ip = client_ip(request)
    return ip if ip != "unknown" else None


def request_user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    agent = (request.headers.get("user-agent") or "").strip()
    return agent[:2000] if agent else None
