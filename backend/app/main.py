import errno
import os
import threading
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.ai.providers import OllamaProvider
from app.ai.registry import ModelRegistry
from app.ai.types import ProviderStatus
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


def _start_parent_watchdog(parent_pid: int) -> None:
    """Monitor parent PID to automatically terminate if the parent desktop host crashes."""

    def _watchdog_loop():
        while True:
            time.sleep(1.0)
            # Check 1: Is parent PID still running?
            try:
                os.kill(parent_pid, 0)
            except OSError as err:
                if err.errno == errno.ESRCH:
                    logger.warning(
                        "Parent process %d terminated (ESRCH). Sidecar self-terminating...",
                        parent_pid,
                    )
                    os._exit(0)
            # Check 2: If directly spawned and adopted by launchd/init (PID 1)
            current_ppid = os.getppid()
            if current_ppid == 1 and parent_pid != 1:
                logger.warning(
                    "Parent process %d adopted by PID 1. Sidecar self-terminating...",
                    parent_pid,
                )
                os._exit(0)

    thread = threading.Thread(target=_watchdog_loop, daemon=True, name="parent-watchdog")
    thread.start()


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

    # 2. Check and activate parent watchdog if managed by desktop host
    parent_pid_env = os.environ.get("ORBIT_PARENT_PID")
    if parent_pid_env and settings.app_env != "testing":
        try:
            parent_pid = int(parent_pid_env)
            if parent_pid > 1:
                logger.info(f"Subprocess watchdog active for parent PID: {parent_pid}")
                _start_parent_watchdog(parent_pid)
        except ValueError:
            pass

    # 3. Initialize database schema
    try:
        await init_db()
    except Exception as e:
        logger.error(f"Fatal error during database initialization: {e}")
        raise

    # 4. Initialize AI Model Registry & local Ollama provider
    registry = ModelRegistry()
    ollama_provider = OllamaProvider()
    try:
        await registry.register_provider(ollama_provider)
        _app.state.model_registry = registry
        _app.state.ollama_provider = ollama_provider

        # Non-blocking health probe on startup
        try:
            health = await ollama_provider.check_health()
            if health.status == ProviderStatus.ONLINE:
                lat = f"{health.latency_ms:.1f}ms" if health.latency_ms is not None else "N/A"
                logger.info(
                    f"AI provider '{ollama_provider.provider_id}' is ONLINE: "
                    f"{health.message} (latency: {lat})"
                )
            else:
                logger.warning(
                    f"AI provider '{ollama_provider.provider_id}' is {health.status}: "
                    f"{health.message}"
                )
        except Exception as err:
            logger.warning(
                f"Initial health check for provider '{ollama_provider.provider_id}' failed: {err}"
            )
    except Exception as exc:
        logger.error(f"Error initializing AI provider subsystem: {exc}")
        await ollama_provider.close()
        raise

    yield

    # 5. Graceful shutdown: close AI provider clients
    logger.info("Closing AI providers...")
    try:
        await ollama_provider.close()
    except Exception as err:
        logger.warning(f"Error closing Ollama provider: {err}")

    # 6. Graceful shutdown: close database connections
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
