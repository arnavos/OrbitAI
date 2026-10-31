"""Health and diagnostic API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_correlation_id
from app.core.security import require_session_auth
from app.db.session import get_db
from app.schemas.common import APIResponse
from app.schemas.system import DetailedHealthResponse, HealthResponse
from app.services.system_service import SystemService

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=APIResponse[HealthResponse])
async def basic_health_check() -> APIResponse[HealthResponse]:
    """Public basic health probe used by the Tauri desktop supervisor for liveness."""
    data = SystemService.get_basic_health()
    return APIResponse[HealthResponse](
        success=True,
        data=data,
        correlation_id=get_correlation_id(),
    )


@router.get(
    "/detailed",
    response_model=APIResponse[DetailedHealthResponse],
    dependencies=[Depends(require_session_auth)],
)
async def detailed_health_check(
    db: AsyncSession = Depends(get_db),
) -> APIResponse[DetailedHealthResponse]:
    """Authenticated detailed health probe with memory, CPU, and SQLite WAL diagnostics."""
    data = await SystemService.get_detailed_health(db=db)
    return APIResponse[DetailedHealthResponse](
        success=True,
        data=data,
        correlation_id=get_correlation_id(),
    )
