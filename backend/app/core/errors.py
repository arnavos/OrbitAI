"""Domain exception hierarchy and global FastAPI error handlers."""

from typing import Any

from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_correlation_id, logger
from app.schemas.common import APIResponse, ErrorDetail


class OrbitError(Exception):
    """Base class for all domain exceptions in ORBIT."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_SERVER_ERROR",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class AuthenticationError(OrbitError):
    """Raised when authentication credentials or session tokens are missing or invalid."""

    def __init__(
        self,
        message: str = "Authentication required or invalid session token",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="AUTHENTICATION_FAILED",
            status_code=status.HTTP_401_UNAUTHORIZED,
            details=details,
        )


class PermissionDeniedError(OrbitError):
    """Raised when caller lacks required permissions or origin is prohibited."""

    def __init__(
        self,
        message: str = "Permission denied or origin prohibited",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="PERMISSION_DENIED",
            status_code=status.HTTP_403_FORBIDDEN,
            details=details,
        )


class NotFoundError(OrbitError):
    """Raised when a requested resource is not found."""

    def __init__(
        self, message: str = "Requested resource not found", details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            code="RESOURCE_NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            details=details,
        )


class ConflictError(OrbitError):
    """Raised when an operation encounters a state conflict or constraint violation."""

    def __init__(
        self,
        message: str = "Resource conflict or constraint violation",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(
            message=message,
            code="RESOURCE_CONFLICT",
            status_code=status.HTTP_409_CONFLICT,
            details=details,
        )


class DatabaseError(OrbitError):
    """Raised when an underlying database operation fails."""

    def __init__(
        self, message: str = "Database operation failed", details: dict[str, Any] | None = None
    ):
        super().__init__(
            message=message,
            code="DATABASE_ERROR",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details=details,
        )


async def orbit_exception_handler(_request: Request, exc: OrbitError) -> JSONResponse:
    """Handle custom OrbitError domain exceptions."""
    correlation_id = get_correlation_id()
    logger.warning(
        f"Domain exception [{exc.code}]: {exc.message}",
        extra={"extra_data": {"status_code": exc.status_code, "details": exc.details}},
    )

    envelope = APIResponse[None](
        success=False,
        data=None,
        error=ErrorDetail(code=exc.code, message=exc.message, details=exc.details),
        correlation_id=correlation_id,
    )
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())


async def validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle FastAPI / Pydantic request validation errors."""
    correlation_id = get_correlation_id()
    logger.info("Request validation failed", extra={"extra_data": {"errors": exc.errors()}})

    envelope = APIResponse[None](
        success=False,
        data=None,
        error=ErrorDetail(
            code="VALIDATION_ERROR",
            message="Request parameter or body validation failed",
            details={"errors": exc.errors()},
        ),
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content=envelope.model_dump()
    )


async def generic_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Fallback handler for unhandled unexpected exceptions."""
    correlation_id = get_correlation_id()
    logger.exception(f"Unhandled server exception: {exc!s}")

    envelope = APIResponse[None](
        success=False,
        data=None,
        error=ErrorDetail(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected server error occurred.",
            details={"error_type": type(exc).__name__},
        ),
        correlation_id=correlation_id,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=envelope.model_dump()
    )
