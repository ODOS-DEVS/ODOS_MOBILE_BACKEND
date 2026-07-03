from pathlib import Path
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
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
    realtime,
    recommendations,
    reviews,
    vouchers,
    vendor,
    wishlist,
)
from app.core.redis_client import close_redis, get_redis
from app.services.realtime_service import realtime_manager
from app.services.vendor_order_reminder_service import process_vendor_order_reminders

app = FastAPI(title="ODOS Mobile Backend")

VENDOR_REMINDER_INTERVAL_SECONDS = 180


async def _vendor_order_reminder_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(process_vendor_order_reminders)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Vendor order reminder loop failed")
        await asyncio.sleep(VENDOR_REMINDER_INTERVAL_SECONDS)


@app.on_event("startup")
async def on_startup() -> None:
    realtime_manager.bind_loop(asyncio.get_running_loop())
    asyncio.create_task(asyncio.to_thread(get_redis))
    asyncio.create_task(_vendor_order_reminder_loop())


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
app.include_router(admin.router, prefix="/api")
app.include_router(wishlist.router, prefix="/api")

app.mount("/uploads", StaticFiles(directory=uploads_directory), name="uploads")
