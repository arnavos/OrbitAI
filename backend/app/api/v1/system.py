"""System diagnostics, configurations, and audit event endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.logging import get_correlation_id
from app.core.security import require_session_auth
from app.db.session import get_db
from app.schemas.common import APIResponse
from app.schemas.system import (
    AuditEventResponse,
    SystemInfoResponse,
    SystemSettingCreate,
    SystemSettingResponse,
)
from app.services.system_service import SystemService

router = APIRouter(prefix="/system", tags=["System"], dependencies=[Depends(require_session_auth)])


@router.get("/info", response_model=APIResponse[SystemInfoResponse])
async def get_system_info(
    db: AsyncSession = Depends(get_db),
) -> APIResponse[SystemInfoResponse]:
    """Retrieve host specifications, platform architecture, and memory capacities."""
    info = await SystemService.get_system_info(db=db)
    return APIResponse[SystemInfoResponse](
        success=True,
        data=info,
        correlation_id=get_correlation_id(),
    )


@router.get("/settings/{key}", response_model=APIResponse[SystemSettingResponse])
async def get_setting(
    key: str,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[SystemSettingResponse]:
    """Retrieve a persistent system configuration setting."""
    setting = await SystemService.get_setting(db=db, key=key)
    if not setting:
        raise NotFoundError(message=f"Configuration setting '{key}' was not found.")
    return APIResponse[SystemSettingResponse](
        success=True,
        data=setting,
        correlation_id=get_correlation_id(),
    )


@router.post("/settings", response_model=APIResponse[SystemSettingResponse])
async def set_setting(
    payload: SystemSettingCreate,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[SystemSettingResponse]:
    """Persist or update a system setting and record an audit log entry."""
    updated = await SystemService.set_setting(
        db=db,
        key=payload.key,
        value=payload.value,
        is_encrypted=payload.is_encrypted,
    )
    return APIResponse[SystemSettingResponse](
        success=True,
        data=updated,
        correlation_id=get_correlation_id(),
    )


@router.get("/audit", response_model=APIResponse[list[AuditEventResponse]])
async def list_audit_events(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[list[AuditEventResponse]]:
    """Retrieve recent immutable audit event records."""
    events = await SystemService.list_audit_events(db=db, limit=limit)
    return APIResponse[list[AuditEventResponse]](
        success=True,
        data=events,
        correlation_id=get_correlation_id(),
    )
