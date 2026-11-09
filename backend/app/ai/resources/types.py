"""Data types, enums, and schemas for the ORBIT resource management subsystem."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MemoryPressureLevel(StrEnum):
    """Normalized operating-system memory pressure levels."""

    NORMAL = "normal"  # XNU level 1 (NOTE_VM_PRESSURE_NORMAL)
    WARNING = "warning"  # XNU level 2 (NOTE_VM_PRESSURE_WARNING)
    CRITICAL = "critical"  # XNU level 4 (NOTE_VM_PRESSURE_CRITICAL)
    UNKNOWN = "unknown"  # Non-Darwin or unobservable state


class AdmissionOutcome(StrEnum):
    """Authoritative outcome of an atomic resource admission evaluation."""

    ALLOW = "allow"
    ALLOW_WITH_REDUCED_CONTEXT = "allow_with_reduced_context"
    REQUIRE_UNLOAD = "require_unload"
    DEFER = "defer"
    REJECT = "reject"


class EstimateConfidence(StrEnum):
    """Confidence classification for model memory estimates."""

    OBSERVED = "observed"  # Direct measurement from running daemon telemetry
    CALIBRATED_MULTI_POINT = "calibrated_multi_point"  # Calibrated against >= 2 observed contexts
    CALIBRATED_SINGLE_POINT = "calibrated_single_point"  # Calibrated against 1 observed context
    THEORETICAL = "theoretical"  # Derived from GGUF GQA tensor metadata
    HEURISTIC = "heuristic"  # Fallback estimate with wide uncertainty bounds


class DirectHostMemory(BaseModel):
    """Raw, authoritative host memory telemetry directly sampled from Darwin kernel/sysctl."""

    total_physical_bytes: int | None = Field(
        default=None, ge=0, description="Hardware physical DRAM (hw.memsize)"
    )
    free_bytes: int | None = Field(default=None, ge=0, description="Truly unallocated Mach pages")
    wired_bytes: int | None = Field(default=None, ge=0, description="Wired memory locked in DRAM")
    active_bytes: int | None = Field(
        default=None, ge=0, description="Pages recently referenced by processes"
    )
    inactive_bytes: int | None = Field(
        default=None, ge=0, description="Pages unreferenced recently (caches/dirty)"
    )
    compressor_bytes: int | None = Field(
        default=None, ge=0, description="DRAM occupied by WKdm compressed pool"
    )
    purgeable_bytes: int | None = Field(
        default=None, ge=0, description="Volatile discardable pages"
    )
    external_page_bytes: int | None = Field(
        default=None, ge=0, description="File-backed pages across Mach queues"
    )
    swap_used_bytes: int | None = Field(
        default=None, ge=0, description="Encrypted swap file usage on SSD"
    )
    swap_total_bytes: int | None = Field(
        default=None, ge=0, description="Total allocated swap space"
    )
    pressure_level: MemoryPressureLevel = Field(..., description="Kernel VM memory pressure level")
    kernel_pressure_raw: int | None = Field(default=None, description="Raw sysctl pressure value")
    measured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_available: bool = Field(
        default=True, description="True if sampled from authoritative host APIs"
    )
    diagnostic_message: str | None = Field(
        default=None, description="Diagnostic details if unavailable or degraded"
    )


class DerivedHeadroom(BaseModel):
    """Derived memory headroom calculation with explicitly documented assumptions."""

    reclaimable_bytes: int = Field(..., ge=0, description="Estimated reclaimable memory")
    effective_host_headroom_bytes: int = Field(..., ge=0, description="Safe headroom above reserve")
    os_safety_reserve_bytes: int = Field(..., ge=0, description="Configured OS reserve floor")
    inactive_reclaim_factor: float = Field(..., ge=0.0, le=1.0, description="Discount factor")
    is_pressure_gated: bool = Field(default=False, description="Gated by critical pressure")
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_conservative: bool = Field(
        default=False, description="True if computed under conservative/degraded stance"
    )


class SystemMemorySnapshot(BaseModel):
    """Consolidated host memory snapshot coupling direct measurements and derived headroom."""

    direct: DirectHostMemory
    derived: DerivedHeadroom
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProcessMemorySnapshot(BaseModel):
    """Process-level memory telemetry sampled via proc_pid_rusage."""

    pid: int
    physical_footprint_bytes: int = Field(..., ge=0, description="ri_phys_footprint metric")
    resident_size_bytes: int = Field(..., ge=0, description="ri_resident_size (RSS)")
    measured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_available: bool = Field(
        default=True, description="True if sampled via native proc_pid_rusage"
    )
    diagnostic_message: str | None = Field(
        default=None, description="Diagnostic details if unavailable"
    )


class MemoryEstimate(BaseModel):
    """Detailed memory requirement estimate with explicit architectural breakdown."""

    model_id: str
    context_length: int
    weights_bytes: int = Field(..., ge=0, description="Mapped model weights in memory")
    kv_cache_bytes: int = Field(..., ge=0, description="Key-value cache footprint for context")
    compute_scratch_bytes: int = Field(..., ge=0, description="Working activation scratch buffer")
    runtime_overhead_bytes: int = Field(..., ge=0, description="Framework and driver overhead")
    total_estimated_bytes: int = Field(..., ge=0, description="Combined total estimated footprint")
    confidence: EstimateConfidence
    source: str
    assumptions: list[str] = Field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        """Convenience alias for total_estimated_bytes."""
        return self.total_estimated_bytes


class ResidentModelAllocation(BaseModel):
    """Layer A: Persistent memory committed to a loaded model in the inference daemon."""

    model_id: str
    base_memory_bytes: int = Field(..., ge=0, description="Base memory (weights + KV cache)")
    verified_context_length: int = Field(..., gt=0, description="Context allocated in daemon")
    loaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    is_external: bool = Field(default=False, description="True if detected via daemon sync")
    source: str = Field(default="observed", description="Telemetry source for allocation size")


class InferenceAllocation(BaseModel):
    """Layer B: Ephemeral memory allocation dedicated to an in-flight inference request."""

    allocation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str
    model_id: str
    allocated_context_length: int = Field(..., gt=0)
    inference_overhead_bytes: int = Field(..., ge=0, description="Activation scratch overhead")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PendingModelLoadReservation(BaseModel):
    """Provisional budget reservation protecting uncommitted allocations during model loading."""

    reservation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    model_id: str
    reserved_base_bytes: int = Field(..., ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime
    requesting_request_id: str
    is_active: bool = Field(default=True)


class ReservationHandle(BaseModel):
    """Composite handle returned on admission authorizing execution and tracking resources."""

    pending_load: PendingModelLoadReservation | None = None
    inference: InferenceAllocation


class AdmissionDecision(BaseModel):
    """Authoritative decision emitted by ResourceManager.acquire_admission()."""

    outcome: AdmissionOutcome
    target_model_id: str
    requested_context: int
    allocated_context: int
    estimated_total_bytes: int
    available_headroom_bytes: int
    handle: ReservationHandle | None = None
    models_to_unload: list[str] = Field(default_factory=list)
    reason: str
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MemoryPressurePolicy(BaseModel):
    """Configurable engineering policy parameters for resource admission and protection."""

    os_safety_reserve_bytes: int = Field(default=3_758_096_384, ge=536_870_912)  # 3.5 GB
    inactive_reclaim_factor: float = Field(default=0.30, ge=0.0, le=1.0)
    min_free_memory_for_load_bytes: int = Field(default=1_073_741_824, ge=0)  # 1 GiB
    default_context_length: int = Field(default=4096, gt=0)
    reduced_context_length: int = Field(default=2048, gt=0)
    min_context_length: int = Field(default=1024, gt=0)
    unload_cooldown_seconds: float = Field(default=30.0, ge=0.0)
    reservation_timeout_seconds: float = Field(default=60.0, gt=0.0)
    pressure_poll_interval_seconds: float = Field(default=3.0, gt=0.0)
    snapshot_freshness_seconds: float = Field(default=1.0, gt=0.0)
