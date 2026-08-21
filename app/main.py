from pathlib import Path
import asyncio
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.middleware.event_logging import EventLoggingMiddleware
from app.routes import (
    admin,
    account,
    assistant,
    auth,
    behavior,
    cart,
    catalog,
    chat,
    customer_wallet,
    delivery,
    health,
    notifications,
    orders,
    payments,
    promo_analytics,
    realtime,
    recommendations,
    reviews,
    vouchers,
    vendor,
    vendor_analytics,
    vendor_campaigns,
    wishlist,
)
from app.core.redis_client import close_redis, get_redis
from app.services.realtime_service import realtime_manager
from app.services.delivery_auto_release_service import process_delivery_auto_release
from app.services.delivery_ops_monitor_service import process_delivery_sla_alerts
from app.services.payment_reconciliation_service import process_stuck_payment_reconciliation
from app.services.promo_reminder_service import process_promo_expiry_reminders
from app.services.vendor_order_reminder_service import process_vendor_order_reminders

app = FastAPI(title="ODOS Mobile Backend")

VENDOR_REMINDER_INTERVAL_SECONDS = 180
DELIVERY_SLA_MONITOR_INTERVAL_SECONDS = 120
PROMO_REMINDER_INTERVAL_SECONDS = 1800
PAYMENT_RECONCILIATION_INTERVAL_SECONDS = 300
DELIVERY_AUTO_RELEASE_INTERVAL_SECONDS = 1800
logger = logging.getLogger(__name__)


def _detail_to_message(detail: object) -> str:
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    if isinstance(detail, list) and detail:
        first = detail[0]
        if isinstance(first, dict):
            msg = first.get("msg")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()
        return "Please check the submitted values."
    if isinstance(detail, dict):
        message = detail.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return "Something went wrong."


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException):
    content: dict[str, object] = {"detail": _detail_to_message(exc.detail)}
    # DeliveryError (and any future business-error subclass) carries a stable
    # machine-readable `code` alongside the human-readable detail, so clients
    # can branch on business-rule violations instead of string-matching.
    error_code = getattr(exc, "code", None)
    if error_code:
        content["code"] = error_code
    return JSONResponse(
        status_code=exc.status_code,
        content=content,
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": _detail_to_message(exc.errors())},
    )


async def _vendor_order_reminder_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_vendor_order_reminders)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Vendor order reminder loop failed")
        await asyncio.sleep(VENDOR_REMINDER_INTERVAL_SECONDS)


async def _delivery_sla_monitor_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_delivery_sla_alerts)
        except Exception:
            logger.exception("Delivery SLA monitor loop failed")
        await asyncio.sleep(DELIVERY_SLA_MONITOR_INTERVAL_SECONDS)


async def _promo_reminder_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_promo_expiry_reminders)
        except Exception:
            logger.exception("Promo expiry reminder loop failed")
        await asyncio.sleep(PROMO_REMINDER_INTERVAL_SECONDS)


async def _payment_reconciliation_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_stuck_payment_reconciliation)
        except Exception:
            logger.exception("Payment reconciliation loop failed")
        await asyncio.sleep(PAYMENT_RECONCILIATION_INTERVAL_SECONDS)


async def _delivery_auto_release_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_delivery_auto_release)
        except Exception:
            logger.exception("Delivery auto-release loop failed")
        await asyncio.sleep(DELIVERY_AUTO_RELEASE_INTERVAL_SECONDS)


@app.on_event("startup")
async def on_startup() -> None:
    realtime_manager.bind_loop(asyncio.get_running_loop())
    asyncio.create_task(asyncio.to_thread(get_redis))
    asyncio.create_task(_vendor_order_reminder_loop())
    asyncio.create_task(_delivery_sla_monitor_loop())
    asyncio.create_task(_promo_reminder_loop())
    asyncio.create_task(_payment_reconciliation_loop())
    asyncio.create_task(_delivery_auto_release_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    close_redis()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(EventLoggingMiddleware)

uploads_directory = Path(settings.media_root)
uploads_directory.mkdir(parents=True, exist_ok=True)

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api/auth")
app.include_router(account.router, prefix="/api")
app.include_router(cart.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(recommendations.router, prefix="/api")
app.include_router(assistant.router, prefix="/api")
app.include_router(delivery.router, prefix="/api")
app.include_router(behavior.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(customer_wallet.router, prefix="/api")
app.include_router(realtime.router, prefix="/api")
app.include_router(reviews.router, prefix="/api")
app.include_router(vouchers.router, prefix="/api")
app.include_router(vendor.router, prefix="/api")
app.include_router(vendor_analytics.router, prefix="/api")
app.include_router(vendor_campaigns.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(promo_analytics.router, prefix="/api")
app.include_router(wishlist.router, prefix="/api")

app.mount("/uploads", StaticFiles(directory=uploads_directory), name="uploads")
