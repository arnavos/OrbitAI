"""API Router aggregation."""

from fastapi import APIRouter

from app.api.v1 import health, system, ws

api_router = APIRouter(prefix="/api/v1")

# Mount sub-routers
api_router.include_router(health.router)
api_router.include_router(system.router)
api_router.include_router(ws.router)
