"""A crashing endpoint must still return CORS headers a browser can read.

Starlette renders unhandled exceptions in ServerErrorMiddleware, which is
always outermost -- above CORSMiddleware. The resulting 500 therefore has no
Access-Control-Allow-Origin, the browser blocks it, and `fetch` rejects with
TypeError: Failed to fetch. Every SPA error handler then reports a server
crash as "the backend is unreachable".

That is not hypothetical: a 380-character store description made vendor
approval raise StringDataRightTruncation, and the admin UI reported the API as
down. The 500 was reaching the browser; the browser just could not read it.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.middleware.unhandled_errors import UnhandledExceptionMiddleware

ORIGIN = "https://g56wepl.odos.market"


def build_app(*, with_fix: bool) -> FastAPI:
    app = FastAPI()

    if with_fix:
        # Registered first => innermost => its response passes back out
        # through CORSMiddleware and picks up the headers.
        app.add_middleware(UnhandledExceptionMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/boom")
    def boom():
        raise ValueError("value too long for type character varying(255)")

    @app.get("/fine")
    def fine():
        return {"ok": True}

    return app


def get(app: FastAPI, path: str):
    client = TestClient(app, raise_server_exceptions=False)
    return client.get(path, headers={"Origin": ORIGIN})


def test_crash_returns_500_with_cors_headers():
    response = get(build_app(with_fix=True), "/boom")
    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == ORIGIN


def test_crash_returns_json_the_client_can_parse():
    response = get(build_app(with_fix=True), "/boom")
    assert response.json() == {"detail": "Something went wrong on our end."}


def test_the_exception_detail_is_not_leaked_to_the_client():
    response = get(build_app(with_fix=True), "/boom")
    assert "character varying" not in response.text


def test_successful_responses_are_unaffected():
    response = get(build_app(with_fix=True), "/fine")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response.headers.get("access-control-allow-origin") == ORIGIN


def test_without_the_middleware_the_500_has_no_cors_headers():
    """Pins the actual defect, so a regression is visible rather than silent."""
    response = get(build_app(with_fix=False), "/boom")
    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") is None
