import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.redis_client import close_redis, get_redis
from app.middleware.event_logging import EventLoggingMiddleware
from app.middleware.unhandled_errors import UnhandledExceptionMiddleware
from app.routes import (
    account,
    admin,
    admin_invitations_public,
    admin_rbac,
    admin_services,
    advanced_analytics,
    assistant,
    auth,
    behavior,
    cart,
    catalog,
    chat,
    courier,
    customer_segmentation,
    customer_wallet,
    delivery,
    email_marketing,
    guides,
    health,
    home_feed,
    loyalty,
    negotiations,
    notifications,
    orders,
    payment_methods,
    payments,
    promo_analytics,
    public_pages,
    realtime,
    recommendations,
    reviews,
    saved_stores,
    services,
    vendor,
    vendor_analytics,
    vendor_campaigns,
    vendor_inventory,
    vouchers,
    wishlist,
)
from app.services.delivery_auto_release_service import process_delivery_auto_release
from app.services.delivery_ops_monitor_service import process_delivery_sla_alerts
from app.services.financial_integrity_service import run_financial_integrity_check
from app.services.payment_reconciliation_service import process_stuck_payment_reconciliation
from app.services.promo_reminder_service import process_promo_expiry_reminders
from app.services.realtime_service import realtime_manager
from app.services.service_escrow import process_booking_escrow_release
from app.services.service_maintenance import process_services_maintenance
from app.services.vendor_order_reminder_service import process_vendor_order_reminders

app = FastAPI(title="ODOS Mobile Backend")

VENDOR_REMINDER_INTERVAL_SECONDS = 180
DELIVERY_SLA_MONITOR_INTERVAL_SECONDS = 120
PROMO_REMINDER_INTERVAL_SECONDS = 1800
NEGOTIATION_REMINDER_INTERVAL_SECONDS = 600
ORDER_EXPIRY_INTERVAL_SECONDS = 300
PAYMENT_RECONCILIATION_INTERVAL_SECONDS = 300
#: Holds release on a 48-hour grace, so a 15-minute sweep is ample
#: precision and keeps the query off the database the rest of the time.
BOOKING_ESCROW_INTERVAL_SECONDS = 900
#: Payment windows are measured in minutes, so this sweeps more
#: often than the escrow release -- a held slot is unsellable.
SERVICES_MAINTENANCE_INTERVAL_SECONDS = 300
DELIVERY_AUTO_RELEASE_INTERVAL_SECONDS = 1800
FINANCIAL_INTEGRITY_INTERVAL_SECONDS = 3600
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


async def _order_lifecycle_loop() -> None:
    while True:
        try:
            from app.services.order_expiry_service import (
                process_order_lifecycle_maintenance,
            )

            await asyncio.to_thread(process_order_lifecycle_maintenance)
        except Exception:
            logger.exception("Order lifecycle maintenance loop failed")
        await asyncio.sleep(ORDER_EXPIRY_INTERVAL_SECONDS)


async def _negotiation_reminder_loop() -> None:
    while True:
        try:
            from app.services.negotiation_reminder_service import (
                process_negotiation_agreement_reminders,
            )

            await asyncio.to_thread(process_negotiation_agreement_reminders)
        except Exception:
            logger.exception("Negotiation agreement reminder loop failed")
        await asyncio.sleep(NEGOTIATION_REMINDER_INTERVAL_SECONDS)


async def _payment_reconciliation_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_stuck_payment_reconciliation)
        except Exception:
            logger.exception("Payment reconciliation loop failed")
        await asyncio.sleep(PAYMENT_RECONCILIATION_INTERVAL_SECONDS)


async def _financial_integrity_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(run_financial_integrity_check)
        except Exception:
            logger.exception("Financial integrity check failed")
        await asyncio.sleep(FINANCIAL_INTEGRITY_INTERVAL_SECONDS)


async def _delivery_auto_release_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_delivery_auto_release)
        except Exception:
            logger.exception("Delivery auto-release loop failed")
        await asyncio.sleep(DELIVERY_AUTO_RELEASE_INTERVAL_SECONDS)


async def _booking_escrow_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_booking_escrow_release)
        except Exception:
            logger.exception("Booking escrow release loop failed")
        await asyncio.sleep(BOOKING_ESCROW_INTERVAL_SECONDS)


async def _services_maintenance_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_services_maintenance)
        except Exception:
            logger.exception("Services maintenance loop failed")
        await asyncio.sleep(SERVICES_MAINTENANCE_INTERVAL_SECONDS)


def _bootstrap_admin_rbac() -> None:
    """Sync the permission catalogue and seed default roles.

    Failure is logged and swallowed on purpose: a seeding problem must not stop
    the API from serving. The existing super admin flag works without any of
    these rows, so the platform stays administrable either way.
    """
    from app.core.database import SessionLocal
    from app.services.admin_rbac_bootstrap import bootstrap_admin_rbac

    db = SessionLocal()
    try:
        bootstrap_admin_rbac(db)
    except Exception:
        logger.exception("Admin RBAC bootstrap failed; continuing without it")
    finally:
        db.close()


@app.on_event("startup")
async def on_startup() -> None:
    # Initialize payment providers
    from app.services.payment_initialization import initialize_payment_providers
    initialize_payment_providers()

    realtime_manager.bind_loop(asyncio.get_running_loop())
    asyncio.create_task(asyncio.to_thread(get_redis))

    # Administrator roles and the permission catalogue. Idempotent and guarded
    # by an advisory lock, so every worker can call it and only one does the
    # work. Deliberately above the scheduler early-return below: access control
    # has to be in place whether or not this process runs the periodic jobs.
    #
    # Awaited, not fired off as a task. A bare asyncio.create_task() is not
    # retained anywhere and can be collected before it runs — which is exactly
    # what happened the first time this was written. The work is two queries
    # once seeded, and serving requests before the permission rows exist is the
    # worse trade.
    await asyncio.to_thread(_bootstrap_admin_rbac)

    # The periodic jobs run EITHER here as asyncio loops, or in a Celery worker driven by
    # beat — never both, or every job fires twice. SCHEDULER_ENABLED defaults to True, so a
    # deployment that knows nothing about Celery behaves exactly as it always has; the Docker
    # Compose stack sets it False on the API and runs the worker/beat services instead.
    if not settings.scheduler_enabled:
        logger.info(
            "In-process scheduler disabled; expecting Celery beat to run the periodic jobs."
        )
        return

    asyncio.create_task(_vendor_order_reminder_loop())
    asyncio.create_task(_delivery_sla_monitor_loop())
    asyncio.create_task(_promo_reminder_loop())
    asyncio.create_task(_negotiation_reminder_loop())
    asyncio.create_task(_order_lifecycle_loop())
    asyncio.create_task(_payment_reconciliation_loop())
    asyncio.create_task(_delivery_auto_release_loop())
    asyncio.create_task(_financial_integrity_loop())
    asyncio.create_task(_booking_escrow_loop())
    asyncio.create_task(_services_maintenance_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    close_redis()

# add_middleware prepends, so the last registered layer is the outermost one.
# UnhandledExceptionMiddleware is registered first and therefore sits *inside*
# CORSMiddleware, which is what lets a 500 carry CORS headers back to a browser.
app.add_middleware(UnhandledExceptionMiddleware)
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

# Support, privacy and terms are served at the site root rather than under
# /api: they are pages a person opens in a browser, and the App Store requires
# a working Support URL and Privacy Policy URL.
app.include_router(public_pages.router)
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
app.include_router(payment_methods.router, prefix="/api")
app.include_router(loyalty.router, prefix="/api")
app.include_router(guides.router, prefix="/api")
app.include_router(guides.admin_router, prefix="/api")
app.include_router(home_feed.router, prefix="/api")
app.include_router(services.router, prefix="/api")
app.include_router(services.bookings_router, prefix="/api")
app.include_router(services.vendor_router, prefix="/api")
app.include_router(services.quotations_router, prefix="/api")
app.include_router(admin_services.router, prefix="/api")
app.include_router(admin_rbac.router, prefix="/api")
# Unauthenticated, token-gated: the account does not exist yet.
app.include_router(admin_invitations_public.router, prefix="/api")
app.include_router(customer_wallet.router, prefix="/api")
app.include_router(realtime.router, prefix="/api")
app.include_router(reviews.router, prefix="/api")
app.include_router(vouchers.router, prefix="/api")
app.include_router(vendor.router, prefix="/api")
app.include_router(courier.router, prefix="/api")
app.include_router(vendor_analytics.router, prefix="/api")
app.include_router(vendor_campaigns.router, prefix="/api")
app.include_router(vendor_inventory.router, prefix="/api")
app.include_router(email_marketing.router, prefix="/api")
app.include_router(customer_segmentation.router, prefix="/api")
app.include_router(advanced_analytics.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(promo_analytics.router, prefix="/api")
app.include_router(negotiations.router, prefix="/api")
app.include_router(saved_stores.router, prefix="/api")
app.include_router(wishlist.router, prefix="/api")

app.mount("/uploads", StaticFiles(directory=uploads_directory), name="uploads")
