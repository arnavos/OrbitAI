"""ORBIT FastAPI Application Entrypoint and Lifespan Orchestration."""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import (
    OrbitError,
    generic_exception_handler,
    orbit_exception_handler,
    validation_exception_handler,
)
from app.core.logging import (
    get_correlation_id,
    logger,
    set_correlation_id,
    setup_logging,
)
from app.db.session import engine, init_db


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware extracting or generating X-Correlation-ID for every HTTP request."""

    async def dispatch(self, request: Request, call_next):
        # Extract incoming correlation ID or generate a new UUIDv4
        corr_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
        set_correlation_id(corr_id)

        response = await call_next(request)
        response.headers["x-correlation-id"] = get_correlation_id()
        return response


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Application startup and graceful shutdown lifespan manager."""
    # 1. Initialize logging
    setup_logging(log_dir=settings.log_dir, log_level=settings.log_level)
    logger.info(
        f"Starting {settings.app_name} v{settings.app_version} in {settings.app_env} mode...",
        extra={"extra_data": {"host": settings.host, "port": settings.port}},
    )

    # 2. Initialize database schema
    try:
        await init_db()
    except Exception as e:
        logger.error(f"Fatal error during database initialization: {e}")
        raise

    yield

    # 3. Graceful shutdown: close database connections
    logger.info("Shutting down database engine...")
    await engine.dispose()
    logger.info("Application shutdown complete.")


def create_application() -> FastAPI:
    """FastAPI application factory."""
    application = FastAPI(
        title="ORBIT Local Backend",
        version=settings.app_version,
        description="Local native backend daemon for the ORBIT platform",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Middlewares
    application.add_middleware(CorrelationIdMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["x-correlation-id"],
    )

    # Exception Handlers
    application.add_exception_handler(OrbitError, orbit_exception_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(Exception, generic_exception_handler)

    # Mount API Routers
    application.include_router(api_router)

    # Legacy root /health alias
    @application.get("/health", include_in_schema=False)
    async def root_health_redirect():
        from app.services.system_service import SystemService

        return {
            "status": "healthy",
            "version": settings.app_version,
            "uptime_seconds": SystemService.get_uptime_seconds(),
        }

    return application


app = create_application()
