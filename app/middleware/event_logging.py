from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.database import SessionLocal
from app.core.event_types import (
    API_REQUEST_FAILED,
    AUTH_FAILURE,
    RATE_LIMIT_TRIGGERED,
)
from app.helpers.event_context import request_ip, request_user_agent
from app.services.event_log_service import record_system_event

logger = logging.getLogger(__name__)

AUDIT_PATH_PREFIXES = ("/api/admin/", "/api/auth/")
SKIP_PATHS = {"/api/health", "/api/ws"}


class EventLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path
        if path in SKIP_PATHS or path.startswith("/uploads"):
            return await call_next(request)

        response = await call_next(request)
        status_code = response.status_code

        if status_code < 400:
            return response

        should_log = (
            status_code == 429
            or status_code in {401, 403}
            or status_code >= 500
        )
        if not should_log:
            return response

        if status_code in {401, 403} and not any(
            path.startswith(prefix) for prefix in AUDIT_PATH_PREFIXES
        ):
            return response

        try:
            db = SessionLocal()
            try:
                metadata = {
                    "method": request.method,
                    "path": path,
                    "status_code": status_code,
                }
                ip = request_ip(request)
                agent = request_user_agent(request)

                if status_code == 429:
                    event_type = RATE_LIMIT_TRIGGERED
                    action = "rate_limit.triggered"
                elif status_code in {401, 403}:
                    event_type = AUTH_FAILURE
                    action = "auth.access_denied"
                else:
                    event_type = API_REQUEST_FAILED
                    action = "api.request_failed"

                record_system_event(
                    db,
                    event_type=event_type,
                    action=action,
                    metadata=metadata,
                    ip_address=ip,
                    user_agent=agent,
                )
            finally:
                db.close()
        except Exception:
            logger.exception("Failed to persist middleware event log")

        return response
