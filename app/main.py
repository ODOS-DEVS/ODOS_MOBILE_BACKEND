from fastapi import FastAPI

from app.routes import account, auth, cart, catalog, health, notifications, orders, wishlist

app = FastAPI(title="ODOS Mobile Backend")

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api/auth")
app.include_router(account.router, prefix="/api")
app.include_router(cart.router, prefix="/api")
app.include_router(catalog.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(orders.router, prefix="/api")
app.include_router(wishlist.router, prefix="/api")
