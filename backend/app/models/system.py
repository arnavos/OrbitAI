"""SQLAlchemy ORM models for system settings, immutable audit logs, and health telemetry."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, utc_now


class SystemSetting(Base, TimestampMixin):
    """Key-value configuration store for application settings and user preferences."""

    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    is_encrypted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class AuditEvent(Base):
    """Append-only, immutable audit log of critical system operations and events."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (Index("ix_audit_events_created_at", "created_at"),)


class HealthSnapshot(Base):
    """Historical records of system health, memory consumption, and database latency."""

    __tablename__ = "health_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cpu_percent: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    memory_rss_mb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    db_latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


# ==============================================================================
# Immutability Guard: ORM-level event listeners for AuditEvent
# ==============================================================================
@event.listens_for(AuditEvent, "before_update")
def _prevent_audit_update(_mapper, _connection, _target):
    raise PermissionError(
        "Security violation: AuditEvent records are append-only and cannot be updated."
    )


@event.listens_for(AuditEvent, "before_delete")
def _prevent_audit_delete(_mapper, _connection, _target):
    raise PermissionError(
        "Security violation: AuditEvent records are append-only and cannot be deleted."
    )
