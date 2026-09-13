"""Turn unhandled exceptions into a JSON response *inside* the CORS layer.

Starlette's ServerErrorMiddleware is always the outermost layer of the stack,
so an exception that escapes a route is rendered into `500 Internal Server
Error` above CORSMiddleware and never receives `Access-Control-Allow-Origin`.
A browser then refuses to read the response and reports it as a network
failure, so every server-side crash reaches the admin and vendor SPAs
indistinguishable from the API being unreachable -- which is how a
`StringDataRightTruncation` on vendor approval was mistaken for an outage.

Registering this *before* CORSMiddleware in app.main puts it underneath, so
the response it returns still travels back out through the CORS layer and
carries the headers a browser needs to read the real status.
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


class UnhandledExceptionMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        try:
            return await call_next(request)
        except Exception:
            # BaseException subclasses that are not Exception -- notably
            # asyncio.CancelledError on client disconnect -- deliberately pass
            # through rather than being reported as a server fault.
            logger.exception(
                "Unhandled exception on %s %s", request.method, request.url.path
            )
            return JSONResponse(
                status_code=500,
                content={"detail": "Something went wrong on our end."},
            )
