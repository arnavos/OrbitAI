"""Service layer managing system telemetry, diagnostics, and settings persistence."""

import json
import os
import platform
import time
from typing import Any

import psutil
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_correlation_id, logger
from app.models.system import AuditEvent, HealthSnapshot, SystemSetting
from app.schemas.system import (
    AuditEventResponse,
    DetailedHealthResponse,
    HealthResponse,
    SystemInfoResponse,
    SystemSettingResponse,
)

# Record process startup timestamp
PROCESS_START_TIME = time.time()


class SystemService:
    """Encapsulates system-level queries, health monitoring, and audit logging."""

    @staticmethod
    def get_uptime_seconds() -> float:
        """Return backend process uptime in seconds."""
        return round(time.time() - PROCESS_START_TIME, 2)

    @classmethod
    def get_basic_health(cls) -> HealthResponse:
        """Return high-level application health status."""
        return HealthResponse(
            status="healthy",
            version=settings.app_version,
            uptime_seconds=cls.get_uptime_seconds(),
        )

    @classmethod
    async def get_detailed_health(cls, db: AsyncSession) -> DetailedHealthResponse:
        """Perform comprehensive health probe including memory, CPU, and database latency."""
        proc = psutil.Process(os.getpid())
        mem_info = proc.memory_info()
        memory_rss_mb = round(mem_info.rss / (1024 * 1024), 2)
        cpu_percent = round(proc.cpu_percent(interval=None), 2)

        # Database latency probe
        db_connected = False
        db_latency_ms = 0.0
        journal_mode = "unknown"

        try:
            t0 = time.perf_counter()
            res = await db.execute(text("PRAGMA journal_mode;"))
            journal_mode = str(res.scalar() or "unknown")
            t1 = time.perf_counter()
            db_latency_ms = round((t1 - t0) * 1000, 2)
            db_connected = True
        except Exception as e:
            logger.error(f"Database health probe failed: {e}")
            db_connected = False

        status_str = "healthy" if db_connected else "degraded"

        # Record health snapshot
        try:
            snapshot = HealthSnapshot(
                status=status_str,
                cpu_percent=cpu_percent,
                memory_rss_mb=memory_rss_mb,
                db_latency_ms=db_latency_ms,
            )
            db.add(snapshot)
            await db.commit()
        except Exception as e:
            logger.warning(f"Could not persist health snapshot: {e}")

        return DetailedHealthResponse(
            status=status_str,
            version=settings.app_version,
            uptime_seconds=cls.get_uptime_seconds(),
            pid=proc.pid,
            memory_rss_mb=memory_rss_mb,
            cpu_percent=cpu_percent,
            database_connected=db_connected,
            database_latency_ms=db_latency_ms,
            journal_mode=journal_mode,
        )

    @classmethod
    async def get_system_info(cls, db: AsyncSession) -> SystemInfoResponse:
        """Retrieve system hardware, OS, and runtime platform metadata."""
        vm = psutil.virtual_memory()

        sqlite_ver = "unknown"
        try:
            res = await db.execute(text("SELECT sqlite_version();"))
            sqlite_ver = str(res.scalar() or "unknown")
        except Exception as e:
            logger.warning(f"Could not query sqlite version: {e}")

        return SystemInfoResponse(
            app_name=settings.app_name,
            app_version=settings.app_version,
            environment=settings.app_env,
            platform=platform.system(),
            architecture=platform.machine(),
            python_version=platform.python_version(),
            sqlite_version=sqlite_ver,
            total_memory_mb=round(vm.total / (1024 * 1024), 2),
            available_memory_mb=round(vm.available / (1024 * 1024), 2),
        )

    @classmethod
    async def get_setting(cls, db: AsyncSession, key: str) -> SystemSettingResponse | None:
        """Fetch a specific configuration setting by key."""
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        result = await db.execute(stmt)
        setting = result.scalar_one_or_none()
        if not setting:
            return None

        try:
            val = json.loads(setting.value_json)
        except Exception:
            val = setting.value_json

        return SystemSettingResponse(
            key=setting.key,
            value=val,
            is_encrypted=setting.is_encrypted,
            updated_at=setting.updated_at,
        )

    @classmethod
    async def set_setting(
        cls, db: AsyncSession, key: str, value: Any, is_encrypted: bool = False
    ) -> SystemSettingResponse:
        """Create or update a system setting and log an audit event."""
        val_json = json.dumps(value)
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        result = await db.execute(stmt)
        setting = result.scalar_one_or_none()

        if setting:
            setting.value_json = val_json
            setting.is_encrypted = is_encrypted
        else:
            setting = SystemSetting(key=key, value_json=val_json, is_encrypted=is_encrypted)
            db.add(setting)

        # Record audit event
        await cls.record_audit_event(
            db=db,
            event_type="SETTING_UPDATED",
            actor="user",
            severity="INFO",
            payload={"key": key, "is_encrypted": is_encrypted},
        )

        await db.commit()
        await db.refresh(setting)

        return SystemSettingResponse(
            key=setting.key,
            value=value,
            is_encrypted=setting.is_encrypted,
            updated_at=setting.updated_at,
        )

    @classmethod
    async def record_audit_event(
        cls,
        db: AsyncSession,
        event_type: str,
        actor: str = "system",
        severity: str = "INFO",
        payload: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Append an immutable audit event to the audit_events table."""
        event_record = AuditEvent(
            correlation_id=get_correlation_id(),
            event_type=event_type,
            actor=actor,
            severity=severity,
            payload_json=json.dumps(payload or {}),
        )
        db.add(event_record)
        return event_record

    @classmethod
    async def list_audit_events(cls, db: AsyncSession, limit: int = 50) -> list[AuditEventResponse]:
        """Fetch latest audit events ordered descending by creation time."""
        stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        records = result.scalars().all()

        out: list[AuditEventResponse] = []
        for r in records:
            try:
                p = json.loads(r.payload_json)
            except Exception:
                p = {}
            out.append(
                AuditEventResponse(
                    id=r.id,
                    correlation_id=r.correlation_id,
                    event_type=r.event_type,
                    actor=r.actor,
                    severity=r.severity,
                    payload=p,
                    created_at=r.created_at,
                )
            )
        return out
