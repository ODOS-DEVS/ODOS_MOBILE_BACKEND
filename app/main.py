from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.routes import (
    admin,
    account,
    auth,
    cart,
    catalog,
    health,
    notifications,
    orders,
    reviews,
    vouchers,
    vendor,
    wishlist,
)

app = FastAPI(title="ODOS Mobile Backend")

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
app.include_router(notifications.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(reviews.router, prefix="/api")
app.include_router(vouchers.router, prefix="/api")
app.include_router(vendor.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(wishlist.router, prefix="/api")

app.mount("/uploads", StaticFiles(directory=uploads_directory), name="uploads")
