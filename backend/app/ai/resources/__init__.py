"""Public exports for the ORBIT AI Resource Management subsystem."""

from app.ai.resources.estimator import ModelMemoryEstimator
from app.ai.resources.manager import ResourceManager
from app.ai.resources.monitor import MacOSMemoryMonitor
from app.ai.resources.types import (
    AdmissionDecision,
    AdmissionOutcome,
    DerivedHeadroom,
    DirectHostMemory,
    EstimateConfidence,
    InferenceAllocation,
    MemoryEstimate,
    MemoryPressureLevel,
    MemoryPressurePolicy,
    PendingModelLoadReservation,
    ProcessMemorySnapshot,
    ReservationHandle,
    ResidentModelAllocation,
    SystemMemorySnapshot,
)

__all__ = [
    "AdmissionDecision",
    "AdmissionOutcome",
    "DerivedHeadroom",
    "DirectHostMemory",
    "EstimateConfidence",
    "InferenceAllocation",
    "MacOSMemoryMonitor",
    "MemoryEstimate",
    "MemoryPressureLevel",
    "MemoryPressurePolicy",
    "ModelMemoryEstimator",
    "PendingModelLoadReservation",
    "ProcessMemorySnapshot",
    "ReservationHandle",
    "ResidentModelAllocation",
    "ResourceManager",
    "SystemMemorySnapshot",
]
