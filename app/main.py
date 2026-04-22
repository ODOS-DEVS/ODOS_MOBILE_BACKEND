from fastapi import FastAPI

from app.routes import health

app = FastAPI(title="ODOS Mobile Backend")

app.include_router(health.router, prefix="/api")
