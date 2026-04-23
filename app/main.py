from fastapi import FastAPI

from app.routes import auth, health

app = FastAPI(title="ODOS Mobile Backend")

app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api/auth")
