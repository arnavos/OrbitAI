"""Deterministic unit and integration tests for ResourceManager and memory safeguards."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from app.ai.resources.estimator import ModelMemoryEstimator
from app.ai.resources.manager import ResourceManager
from app.ai.resources.monitor import MacOSMemoryMonitor
from app.ai.resources.types import (
    AdmissionDecision,
    AdmissionOutcome,
    DerivedHeadroom,
    DirectHostMemory,
    MemoryPressureLevel,
    MemoryPressurePolicy,
    SystemMemorySnapshot,
)


def _make_mock_snapshot(
    headroom_bytes: int,
    pressure: MemoryPressureLevel = MemoryPressureLevel.NORMAL,
    free_bytes: int | None = None,
    timestamp: datetime | None = None,
    is_available: bool = True,
    diagnostic_message: str | None = None,
) -> SystemMemorySnapshot:
    now = timestamp or datetime.now(UTC)
    fb = headroom_bytes if free_bytes is None else free_bytes
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3) if is_available else None,
        free_bytes=fb if is_available else None,
        active_bytes=4 * (1024**3) if is_available else None,
        inactive_bytes=2 * (1024**3) if is_available else None,
        wired_bytes=3 * (1024**3) if is_available else None,
        compressor_bytes=0,
        purgeable_bytes=0,
        external_page_bytes=0,
        swap_used_bytes=0,
        swap_total_bytes=0,
        pressure_level=pressure,
        kernel_pressure_raw=1 if pressure == MemoryPressureLevel.NORMAL else 4,
        is_available=is_available,
        diagnostic_message=diagnostic_message,
        measured_at=now,
    )
    derived = DerivedHeadroom(
        reclaimable_bytes=headroom_bytes if is_available else 0,
        effective_host_headroom_bytes=headroom_bytes if is_available else 0,
        os_safety_reserve_bytes=0,
        inactive_reclaim_factor=0.30 if is_available else 0.0,
        is_pressure_gated=pressure == MemoryPressureLevel.CRITICAL,
        is_conservative=not is_available or pressure == MemoryPressureLevel.UNKNOWN,
        computed_at=now,
    )
    return SystemMemorySnapshot(direct=direct, derived=derived, timestamp=now)


@pytest.fixture
def mock_monitor():
    monitor = MagicMock(spec=MacOSMemoryMonitor)
    # Default 4 GB headroom
    monitor.get_system_snapshot.return_value = _make_mock_snapshot(4 * (1024**3))
    return monitor


@pytest.fixture
def estimator():
    est = ModelMemoryEstimator()
    # Cache fixed arch metadata for llama3.2:3b (approx 2.0 GB weights + 224 MB KV at ctx 2048)
    arch = {
        "size": 2_000_000_000,
        "model_info": {
            "general.architecture": "llama",
            "llama.block_count": 28,
            "llama.attention.head_count_kv": 8,
            "llama.attention.key_length": 128,
        },
    }
    est.cache_model_architecture("llama3.2:3b", arch)
    return est


@pytest.mark.asyncio
async def test_atomic_admission_and_concurrency(mock_monitor, estimator):
    """Verify concurrent requests cannot oversubscribe memory budget."""
    # Constrain headroom to 3.0 GB: exactly one 3B model load (approx 2.3 GB) can fit
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(3_000_000_000)

    # Cache arch metadata for models so each requires approx 2.3 GB
    arch = {
        "size": 2_000_000_000,
        "model_info": {
            "general.architecture": "llama",
            "llama.block_count": 28,
            "llama.attention.head_count_kv": 8,
            "llama.attention.key_length": 128,
        },
    }
    for i in range(5):
        estimator.cache_model_architecture(f"model_{i}:latest", arch)

    policy = MemoryPressurePolicy(default_context_length=2048)
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator, policy=policy)

    # Launch 5 concurrent admission requests for different models
    async def try_admit(req_idx: int) -> AdmissionDecision:
        return await manager.acquire_admission(
            model_id=f"model_{req_idx}:latest",
            request_id=f"req_{req_idx}",
            requested_context=2048,
        )

    results = await asyncio.gather(*(try_admit(i) for i in range(5)))

    # Exactly one request must be admitted, others deferred or rejected
    admitted = [
        r
        for r in results
        if r.outcome in (AdmissionOutcome.ALLOW, AdmissionOutcome.ALLOW_WITH_REDUCED_CONTEXT)
    ]
    not_admitted = [
        r for r in results if r.outcome in (AdmissionOutcome.DEFER, AdmissionOutcome.REJECT)
    ]

    assert len(admitted) == 1
    assert len(not_admitted) == 4
    assert len(manager._pending_loads) == 1


@pytest.mark.asyncio
async def test_layer_a_retention_and_layer_b_cleanup(mock_monitor, estimator):
    """Verify Layer A resident model is retained while Layer B is released on completion."""
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(5_000_000_000)
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    # Run inference in context manager
    async with manager.reserve_inference("llama3.2:3b", "req-1", 2048) as decision:
        assert decision.outcome == AdmissionOutcome.ALLOW
        assert "req-1" in manager._active_inferences
        # Simulate model confirmation on first token
        await manager.confirm_model_loaded("llama3.2:3b", verified_context=2048)

    # After context exit: Layer B cleaned up, Layer A retained
    assert "req-1" not in manager._active_inferences
    assert "llama3.2:3b" in manager._resident_models
    assert len(manager._pending_loads) == 0

    # Second inference request for already-resident model: requires 0 pending base bytes
    dec2 = await manager.acquire_admission("llama3.2:3b", "req-2", 2048)
    assert dec2.outcome == AdmissionOutcome.ALLOW
    assert dec2.handle.pending_load is None  # No pending base load needed


@pytest.mark.asyncio
async def test_context_reduction_propagation(mock_monitor, estimator):
    """Verify context length is reduced when full context exceeds headroom but reduced fits."""
    # Headroom allows 2048 context (approx 2.3 GB) but not 4096 context (approx 2.6 GB)
    # Total for ctx 4096 is ~2.59 GB; set headroom to 2.45 GB
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(2_450_000_000)

    policy = MemoryPressurePolicy(
        default_context_length=4096,
        reduced_context_length=2048,
        min_context_length=1024,
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator, policy=policy)

    decision = await manager.acquire_admission("llama3.2:3b", "req-reduce", requested_context=4096)
    assert decision.outcome == AdmissionOutcome.ALLOW_WITH_REDUCED_CONTEXT
    assert decision.allocated_context == 2048
    assert decision.requested_context == 4096


@pytest.mark.asyncio
async def test_two_phase_eviction_execution(mock_monitor, estimator):
    """Verify idle model is evicted out-of-lock and replaced with candidate."""
    # Initial state: Model A is resident in Layer A, taking 2.5 GB
    # Headroom is 1.0 GB. Model B needs 2.3 GB.
    # Evicting Model A will yield provisional headroom 1.0 + 2.5 = 3.5 GB > 2.3 GB.
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(1_000_000_000)
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    # Setup resident model A
    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )
    assert "model_a:latest" in manager._resident_models

    # Define mock provider unload
    unloaded_models = []

    async def mock_unload(model_id: str) -> bool:
        unloaded_models.append(model_id)
        # Simulate memory freeing on host after unload: fresh headroom becomes 3.5 GB
        mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(3_500_000_000)
        return True

    decision = await manager.execute_two_phase_admission(
        model_id="llama3.2:3b",
        request_id="req-evict",
        requested_context=2048,
        provider_unload_callable=mock_unload,
    )

    assert "model_a:latest" in unloaded_models
    assert decision.outcome == AdmissionOutcome.ALLOW
    assert "model_a:latest" not in manager._resident_models


@pytest.mark.asyncio
async def test_active_inference_lease_blocks_eviction(mock_monitor, estimator):
    """Verify resident model with active inference lease is NEVER evicted."""
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(500_000_000)
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    # Model A is resident AND has an active inference lease
    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )
    # Commit an active lease for model_a
    dec_a = await manager.acquire_admission("model_a:latest", "lease-a", requested_context=2048)
    assert dec_a.outcome == AdmissionOutcome.ALLOW

    # Now attempt admission for Model B which needs eviction
    dec_b = await manager.acquire_admission("llama3.2:3b", "req-b", requested_context=2048)
    # Model A cannot be evicted because lease is active; outcome must be DEFER
    assert dec_b.outcome == AdmissionOutcome.DEFER


@pytest.mark.asyncio
async def test_critical_memory_pressure_rejection(mock_monitor, estimator):
    """Verify critical memory pressure immediately blocks admission."""
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=0,
        pressure=MemoryPressureLevel.CRITICAL,
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    decision = await manager.acquire_admission("llama3.2:3b", "req-crit", requested_context=2048)
    assert decision.outcome == AdmissionOutcome.REJECT
    assert "critical memory pressure" in decision.reason.lower()


@pytest.mark.asyncio
async def test_daemon_reconciliation_loads_and_unloads(mock_monitor, estimator):
    """Verify reconcile_with_daemon updates Layer A for external loads and unloads."""
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    # 1. External load discovered in /api/ps
    raw_ps = [{"model": "external_model:latest", "size_vram": 2_147_483_648}]
    await manager.reconcile_with_daemon(raw_ps)

    assert "external_model:latest" in manager._resident_models
    assert manager._resident_models["external_model:latest"].is_external

    # 2. External unload: /api/ps is now empty
    await manager.reconcile_with_daemon([])
    assert "external_model:latest" not in manager._resident_models


@pytest.mark.asyncio
async def test_expired_pending_reservation_sweep(mock_monitor, estimator):
    """Verify abandoned pending reservations are swept when expired."""
    policy = MemoryPressurePolicy(reservation_timeout_seconds=0.1)  # 100ms timeout
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator, policy=policy)

    # Acquire admission committing a pending reservation
    dec = await manager.acquire_admission("llama3.2:3b", "req-abandoned", 2048)
    assert dec.outcome == AdmissionOutcome.ALLOW
    assert len(manager._pending_loads) == 1

    # Before TTL expiration, sweep does not touch it
    swept = await manager.sweep_expired_reservations()
    assert swept == []
    assert len(manager._pending_loads) == 1

    # Wait for expiration
    await asyncio.sleep(0.15)

    # Sweep expires it
    swept = await manager.sweep_expired_reservations()
    assert len(swept) == 1
    assert len(manager._pending_loads) == 0


@pytest.mark.asyncio
async def test_conservative_min_free_memory_rejection_for_new_load(mock_monitor, estimator):
    """Verify new model load is rejected when verified free memory is below 1 GiB threshold."""
    # Free memory 500 MB (< 1073741824 bytes), total headroom 500 MB, no resident models
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(500_000_000)
    policy = MemoryPressurePolicy(min_free_memory_for_load_bytes=1_073_741_824)
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator, policy=policy)

    dec = await manager.acquire_admission("llama3.2:3b", "req-reject-low-free", 2048)
    assert dec.outcome == AdmissionOutcome.REJECT
    assert "below the conservative model-load threshold" in dec.reason.lower()


@pytest.mark.asyncio
async def test_conservative_min_free_memory_unavailable_telemetry(estimator):
    """Verify new model load is rejected when required telemetry cannot verify free memory."""
    now = datetime.now(UTC)
    direct = DirectHostMemory(
        total_physical_bytes=None,
        free_bytes=None,
        pressure_level=MemoryPressureLevel.UNKNOWN,
        is_available=False,
        diagnostic_message="Mach host port closed",
        measured_at=now,
    )
    derived = DerivedHeadroom(
        reclaimable_bytes=0,
        effective_host_headroom_bytes=0,
        os_safety_reserve_bytes=1024**3,
        inactive_reclaim_factor=0.0,
        is_conservative=True,
        computed_at=now,
    )
    mock_mon = MagicMock(spec=MacOSMemoryMonitor)
    mock_mon.get_system_snapshot.return_value = SystemMemorySnapshot(
        direct=direct, derived=derived, timestamp=now
    )

    manager = ResourceManager(monitor=mock_mon, estimator=estimator)
    dec = await manager.acquire_admission("llama3.2:3b", "req-reject-unavail", 2048)

    assert dec.outcome == AdmissionOutcome.REJECT
    assert "telemetry unavailable" in dec.reason.lower()
    assert "Mach host port closed" in dec.reason


@pytest.mark.asyncio
async def test_already_resident_model_admitted_with_low_free_memory(estimator):
    """Verify already-resident model is admitted when scratch fits, without double-counting."""
    now = datetime.now(UTC)
    # Free memory is only 500 MB (below 1 GiB), but purgeable/inactive cache provides 1.5 GB headroom
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),
        free_bytes=500_000_000,
        active_bytes=6 * (1024**3),
        inactive_bytes=4 * (1024**3),
        wired_bytes=3 * (1024**3),
        compressor_bytes=0,
        purgeable_bytes=1_500_000_000,
        external_page_bytes=0,
        swap_used_bytes=0,
        swap_total_bytes=0,
        pressure_level=MemoryPressureLevel.NORMAL,
        is_available=True,
        measured_at=now,
    )
    derived = DerivedHeadroom(
        reclaimable_bytes=1_500_000_000,
        effective_host_headroom_bytes=1_500_000_000,
        os_safety_reserve_bytes=0,
        inactive_reclaim_factor=0.30,
        is_conservative=False,
        computed_at=now,
    )
    mock_mon = MagicMock(spec=MacOSMemoryMonitor)
    mock_mon.get_system_snapshot.return_value = SystemMemorySnapshot(
        direct=direct, derived=derived, timestamp=now
    )

    manager = ResourceManager(monitor=mock_mon, estimator=estimator)

    # Confirm llama3.2:3b is already resident
    await manager.confirm_model_loaded(
        "llama3.2:3b", verified_context=2048, observed_size_bytes=2_000_000_000
    )
    assert "llama3.2:3b" in manager._resident_models

    # Admission for resident model only needs scratch buffer (~100 MB), which fits in 1.5 GB headroom
    dec = await manager.acquire_admission("llama3.2:3b", "req-resident-scratch", 2048)
    assert dec.outcome == AdmissionOutcome.ALLOW
    assert dec.handle.pending_load is None
    assert dec.handle.inference.inference_overhead_bytes > 0


@pytest.mark.asyncio
async def test_idle_model_allocation_never_added_to_free_or_headroom_before_unload(
    mock_monitor, estimator
):
    """Verify an idle model's estimated allocation is NEVER added to free memory or headroom before unload."""
    # Headroom and free memory are both 800 MB (below 1 GiB load threshold)
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=800_000_000, free_bytes=800_000_000
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    # Resident model A occupies 2.5 GB
    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )
    assert "model_a:latest" in manager._resident_models

    # Evaluate admission for llama3.2:3b (requires ~2.3 GB)
    decision = await manager.acquire_admission("llama3.2:3b", "req-no-speculative", 2048)

    # Must require unload without speculative credit
    assert decision.outcome == AdmissionOutcome.REQUIRE_UNLOAD
    assert decision.models_to_unload == ["model_a:latest"]
    # Available headroom must report EXACT un-inflated measured headroom, NOT 800 MB + 2.5 GB
    assert decision.available_headroom_bytes == 800_000_000
    # No reservation must be committed
    assert decision.handle is None


@pytest.mark.asyncio
async def test_successful_unload_triggers_fresh_telemetry_read_before_admission(
    mock_monitor, estimator
):
    """Verify successful unload triggers a fresh telemetry read from host before admission."""
    # Initial state: 1.0 GB free and 1.0 GB headroom
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=1_000_000_000, free_bytes=1_000_000_000
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )

    call_count_before_unload = 0

    async def mock_unload(model_id: str) -> bool:
        nonlocal call_count_before_unload
        call_count_before_unload = mock_monitor.get_system_snapshot.call_count
        # Set fresh host snapshot reflecting 3.5 GB free and headroom
        mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
            headroom_bytes=3_500_000_000, free_bytes=3_500_000_000
        )
        return True

    decision = await manager.execute_two_phase_admission(
        model_id="llama3.2:3b",
        request_id="req-fresh-telemetry",
        requested_context=2048,
        provider_unload_callable=mock_unload,
    )

    # Must have sampled fresh telemetry post-unload
    assert mock_monitor.get_system_snapshot.call_count > call_count_before_unload
    assert decision.outcome == AdmissionOutcome.ALLOW
    assert decision.available_headroom_bytes == 3_500_000_000
    assert decision.handle is not None


@pytest.mark.asyncio
async def test_admission_rejected_when_fresh_headroom_remains_below_1_gib(mock_monitor, estimator):
    """Verify admission is rejected when fresh post-unload available headroom remains below 1 GiB."""
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=800_000_000, free_bytes=800_000_000
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )

    async def mock_unload(model_id: str) -> bool:
        # Host memory after unload: available headroom is only 900 MB (< 1 GiB threshold)
        mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
            headroom_bytes=900_000_000, free_bytes=90_000_000
        )
        return True

    decision = await manager.execute_two_phase_admission(
        model_id="llama3.2:3b",
        request_id="req-low-headroom-post",
        requested_context=2048,
        provider_unload_callable=mock_unload,
    )

    # Must be rejected because verified available headroom < 1 GiB
    assert decision.outcome == AdmissionOutcome.REJECT
    assert "below the conservative model-load threshold" in decision.reason.lower()
    assert decision.handle is None


@pytest.mark.asyncio
async def test_admission_admitted_when_fresh_raw_free_is_low_but_reclaimable_headroom_sufficient(
    mock_monitor, estimator
):
    """Verify post-unload admission succeeds when raw free is low (~90MB) but reclaimable headroom is 3.5GB."""
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=800_000_000, free_bytes=800_000_000
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )

    async def mock_unload(model_id: str) -> bool:
        # Typical Darwin behavior: unallocated free is only 90 MB, but reclaimable headroom is 3.5 GB
        mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
            headroom_bytes=3_500_000_000, free_bytes=90_000_000
        )
        return True

    decision = await manager.execute_two_phase_admission(
        model_id="llama3.2:3b",
        request_id="req-low-free-high-headroom-post",
        requested_context=2048,
        provider_unload_callable=mock_unload,
    )

    # Must be admitted because effective headroom (3.5 GB) >= 1 GiB and >= model footprint
    assert decision.outcome == AdmissionOutcome.ALLOW
    assert decision.handle is not None
    assert decision.available_headroom_bytes == 3_500_000_000


@pytest.mark.asyncio
async def test_admission_rejected_when_fresh_headroom_passes_1_gib_threshold_but_model_does_not_fit(
    mock_monitor, estimator
):
    """Verify admission is rejected when fresh headroom passes 1 GiB threshold but is insufficient to fit model."""
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=800_000_000, free_bytes=800_000_000
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )

    async def mock_unload(model_id: str) -> bool:
        # Headroom is 1.5 GB (>= 1 GiB load threshold), but model requires ~2.3 GB
        mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
            headroom_bytes=1_500_000_000, free_bytes=90_000_000
        )
        return True

    decision = await manager.execute_two_phase_admission(
        model_id="llama3.2:3b",
        request_id="req-insufficient-headroom-post",
        requested_context=2048,
        provider_unload_callable=mock_unload,
    )

    # Must be rejected because effective headroom < required
    assert decision.outcome == AdmissionOutcome.REJECT
    assert "exceeds headroom" in decision.reason.lower()
    assert decision.handle is None


@pytest.mark.asyncio
async def test_unload_failure_never_results_in_admission(mock_monitor, estimator):
    """Verify provider unload failure never results in admission and cleans up evicting state."""
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=800_000_000, free_bytes=800_000_000
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )

    async def mock_unload_fail(model_id: str) -> bool:
        return False

    decision = await manager.execute_two_phase_admission(
        model_id="llama3.2:3b",
        request_id="req-unload-fail",
        requested_context=2048,
        provider_unload_callable=mock_unload_fail,
    )

    assert decision.outcome == AdmissionOutcome.DEFER
    assert decision.handle is None
    assert "model_a:latest" not in manager._evicting_models


@pytest.mark.asyncio
async def test_missing_or_stale_post_unload_telemetry_never_results_in_admission(
    mock_monitor, estimator
):
    """Verify missing or stale post-unload telemetry never results in admission."""
    from datetime import timedelta

    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=800_000_000, free_bytes=800_000_000
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )

    # Scenario 1: Missing / unavailable telemetry after unload
    async def mock_unload_unavail(model_id: str) -> bool:
        mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
            headroom_bytes=0,
            free_bytes=None,
            is_available=False,
            diagnostic_message="Sampling failed post-unload",
        )
        return True

    decision1 = await manager.execute_two_phase_admission(
        model_id="llama3.2:3b",
        request_id="req-unavail-post",
        requested_context=2048,
        provider_unload_callable=mock_unload_unavail,
    )
    assert decision1.outcome == AdmissionOutcome.REJECT
    assert "unavailable" in decision1.reason.lower()
    assert decision1.handle is None

    # Reset resident model A and snapshot to initial state
    manager._unload_history.clear()
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=800_000_000, free_bytes=800_000_000
    )
    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )

    # Scenario 2: Stale telemetry (timestamp precedes unload)
    async def mock_unload_stale(model_id: str) -> bool:
        stale_time = datetime.now(UTC) - timedelta(seconds=10)
        mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
            headroom_bytes=3_500_000_000, free_bytes=3_500_000_000, timestamp=stale_time
        )
        return True

    decision2 = await manager.execute_two_phase_admission(
        model_id="llama3.2:3b",
        request_id="req-stale-post",
        requested_context=2048,
        provider_unload_callable=mock_unload_stale,
    )
    assert decision2.outcome == AdmissionOutcome.REJECT
    assert "stale" in decision2.reason.lower()
    assert decision2.handle is None


@pytest.mark.asyncio
async def test_concurrent_admission_requests_cannot_both_consume_same_eviction_candidate(
    mock_monitor, estimator
):
    """Verify concurrent admission requests cannot both target and consume the same eviction candidate."""
    # Headroom and free memory 800 MB (< 1 GiB). Only one idle model exists.
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=800_000_000, free_bytes=800_000_000
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    await manager.confirm_model_loaded(
        "model_a:latest", verified_context=2048, observed_size_bytes=2_500_000_000
    )

    # Cache arch metadata for second model
    arch = {
        "size": 1_000_000_000,
        "model_info": {
            "general.architecture": "llama",
            "llama.block_count": 16,
            "llama.attention.head_count_kv": 4,
            "llama.attention.key_length": 64,
        },
    }
    estimator.cache_model_architecture("model_b:latest", arch)

    r1_unloading = asyncio.Event()
    r1_can_finish = asyncio.Event()
    unloaded_list = []

    async def mock_unload(model_id: str) -> bool:
        unloaded_list.append(model_id)
        r1_unloading.set()
        await r1_can_finish.wait()
        mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
            headroom_bytes=3_500_000_000, free_bytes=3_500_000_000
        )
        return True

    # Start R1 in background
    r1_task = asyncio.create_task(
        manager.execute_two_phase_admission(
            model_id="llama3.2:3b",
            request_id="req-concurrent-1",
            requested_context=2048,
            provider_unload_callable=mock_unload,
        )
    )

    # Wait until R1 has marked model_a as evicting and entered unload
    await r1_unloading.wait()

    # Now launch R2 while R1 is in flight
    r2_decision = await manager.execute_two_phase_admission(
        model_id="model_b:latest",
        request_id="req-concurrent-2",
        requested_context=2048,
        provider_unload_callable=mock_unload,
    )

    # R2 CANNOT consume model_a because it is already evicting!
    assert r2_decision.outcome in (AdmissionOutcome.DEFER, AdmissionOutcome.REJECT)
    assert r2_decision.handle is None

    # Let R1 finish
    r1_can_finish.set()
    r1_decision = await r1_task

    assert r1_decision.outcome == AdmissionOutcome.ALLOW
    assert r1_decision.handle is not None
    # Model A was unloaded exactly once
    assert unloaded_list == ["model_a:latest"]


@pytest.mark.asyncio
async def test_caller_cancellation_preserves_pending_load_reservation_until_sweep(
    mock_monitor,
    estimator,
):
    """Verify caller cancellation preserves pending load reservation to prevent double-spending."""
    from datetime import timedelta

    # Free memory: 3.0 GB, Model needs ~2.3 GB
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=3_000_000_000, free_bytes=3_000_000_000
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    # R1 acquires admission
    d1 = await manager.acquire_admission("llama3.2:3b", "req-r1", 2048)
    assert d1.outcome == AdmissionOutcome.ALLOW
    assert len(manager._pending_loads) == 1

    # Caller R1 cancels while model is still loading in daemon.
    # release_inference removes Layer B active inference allocation, but preserves pending load reservation.
    await manager.release_inference("req-r1")
    assert "req-r1" not in manager._active_inferences
    assert len(manager._pending_loads) == 1

    # R2 attempts admission for another model requiring 2.3 GB.
    # Because pending reservation for R1 still counts against budget, R2 cannot double-spend headroom!
    d2 = await manager.acquire_admission("llama3.2:3b", "req-r2", 2048)
    assert d2.outcome in (AdmissionOutcome.DEFER, AdmissionOutcome.REJECT)

    # Now simulate sweep expiring R1's pending reservation
    for res in manager._pending_loads.values():
        res.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    swept = await manager.sweep_expired_reservations()
    assert len(swept) == 1
    assert len(manager._pending_loads) == 0

    # Now R3 can be admitted
    d3 = await manager.acquire_admission("llama3.2:3b", "req-r3", 2048)
    assert d3.outcome == AdmissionOutcome.ALLOW


@pytest.mark.asyncio
async def test_manager_repeated_start_stop_cycles(mock_monitor, estimator):
    """Verify start() and stop() can be called repeatedly and cleanly without task leaks."""
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    for _ in range(3):
        await manager.start()
        assert manager._is_running is True
        assert manager._background_task is not None
        # Idempotent start
        await manager.start()

        # Stop
        await manager.stop()
        assert manager._is_running is False
        assert manager._background_task is None
        # Idempotent stop
        await manager.stop()


@pytest.mark.asyncio
async def test_reconciliation_telemetry_failure_isolation(mock_monitor, estimator):
    """Verify daemon reconciliation functions correctly even during abnormal telemetry states."""
    # Snapshot is unavailable
    mock_monitor.get_system_snapshot.return_value = _make_mock_snapshot(
        headroom_bytes=0,
        is_available=False,
        free_bytes=None,
        diagnostic_message="Host offline",
    )
    manager = ResourceManager(monitor=mock_monitor, estimator=estimator)

    ps_report = [
        {
            "model": "deepseek-coder:6.7b",
            "size_vram": 4_000_000_000,
            "context_length": 4096,
        }
    ]
    await manager.reconcile_with_daemon(ps_report)

    assert "deepseek-coder:6.7b" in manager._resident_models
    alloc = manager._resident_models["deepseek-coder:6.7b"]
    assert alloc.base_memory_bytes == 4_000_000_000
    assert alloc.is_external is True

    # Now daemon reports model is gone
    await manager.reconcile_with_daemon([])
    assert "deepseek-coder:6.7b" not in manager._resident_models


@pytest.mark.asyncio
async def test_darwin_cold_load_admitted_with_low_raw_free_pages_and_sufficient_reclaimable_headroom(
    estimator,
):
    """Verify cold model load is admitted on Darwin when raw free pages are ~90MB but reclaimable headroom is ~3GB."""
    now = datetime.now(UTC)
    # Realistic Darwin 16GB snapshot: 90 MB raw free, 5.5 GB inactive, 300 MB purgeable
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),
        free_bytes=90 * (1024**2),
        active_bytes=6 * (1024**3),
        inactive_bytes=5500 * (1024**2),
        wired_bytes=3 * (1024**3),
        compressor_bytes=500 * (1024**2),
        purgeable_bytes=300 * (1024**2),
        external_page_bytes=0,
        swap_used_bytes=0,
        swap_total_bytes=0,
        pressure_level=MemoryPressureLevel.NORMAL,
        kernel_pressure_raw=1,
        is_available=True,
        measured_at=now,
    )
    derived = DerivedHeadroom(
        reclaimable_bytes=4_000_000_000,
        effective_host_headroom_bytes=3_000_000_000,
        os_safety_reserve_bytes=1_073_741_824,
        inactive_reclaim_factor=0.70,
        is_conservative=False,
        computed_at=now,
    )
    mock_mon = MagicMock(spec=MacOSMemoryMonitor)
    mock_mon.get_system_snapshot.return_value = SystemMemorySnapshot(
        direct=direct, derived=derived, timestamp=now
    )
    policy = MemoryPressurePolicy(
        os_safety_reserve_bytes=1_073_741_824,
        min_free_memory_for_load_bytes=1_073_741_824,
    )
    manager = ResourceManager(monitor=mock_mon, estimator=estimator, policy=policy)

    # Cold model load for llama3.2:3b (not resident)
    decision = await manager.acquire_admission("llama3.2:3b", "req-darwin-cold-allow", 2048)
    assert decision.outcome == AdmissionOutcome.ALLOW
    assert decision.handle is not None
    assert decision.handle.pending_load is not None
    assert decision.handle.pending_load.reserved_base_bytes > 0
    assert "admitted" in decision.reason.lower()


@pytest.mark.asyncio
async def test_darwin_cold_load_rejected_with_low_raw_free_pages_and_insufficient_reclaimable_headroom(
    estimator,
):
    """Verify cold model load is rejected when raw free is low AND verified reclaimable headroom is below threshold."""
    now = datetime.now(UTC)
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),
        free_bytes=90 * (1024**2),
        active_bytes=12 * (1024**3),
        inactive_bytes=800 * (1024**2),
        wired_bytes=3 * (1024**3),
        compressor_bytes=500 * (1024**2),
        purgeable_bytes=50 * (1024**2),
        external_page_bytes=0,
        swap_used_bytes=0,
        swap_total_bytes=0,
        pressure_level=MemoryPressureLevel.NORMAL,
        kernel_pressure_raw=1,
        is_available=True,
        measured_at=now,
    )
    derived = DerivedHeadroom(
        reclaimable_bytes=700_000_000,
        effective_host_headroom_bytes=0,
        os_safety_reserve_bytes=1_073_741_824,
        inactive_reclaim_factor=0.70,
        is_conservative=False,
        computed_at=now,
    )
    mock_mon = MagicMock(spec=MacOSMemoryMonitor)
    mock_mon.get_system_snapshot.return_value = SystemMemorySnapshot(
        direct=direct, derived=derived, timestamp=now
    )
    policy = MemoryPressurePolicy(
        os_safety_reserve_bytes=1_073_741_824,
        min_free_memory_for_load_bytes=1_073_741_824,
    )
    manager = ResourceManager(monitor=mock_mon, estimator=estimator, policy=policy)

    decision = await manager.acquire_admission("llama3.2:3b", "req-darwin-cold-reject", 2048)
    assert decision.outcome == AdmissionOutcome.REJECT
    assert "below the conservative model-load threshold" in decision.reason.lower()
    assert decision.handle is None


@pytest.mark.asyncio
async def test_darwin_cold_load_rejected_when_os_reserve_exceeds_reclaimable(estimator):
    """Verify OS safety reserve floor strictly protects memory and causes rejection when reclaimable is tight."""
    now = datetime.now(UTC)
    # Reclaimable is 1.5 GB, but OS reserve is 1.0 GB -> headroom is only 500 MB (< 1 GiB threshold)
    derived = DerivedHeadroom(
        reclaimable_bytes=1_500_000_000,
        effective_host_headroom_bytes=500_000_000,
        os_safety_reserve_bytes=1_000_000_000,
        inactive_reclaim_factor=0.70,
        is_conservative=False,
        computed_at=now,
    )
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),
        free_bytes=90 * (1024**2),
        is_available=True,
        pressure_level=MemoryPressureLevel.NORMAL,
        measured_at=now,
    )
    mock_mon = MagicMock(spec=MacOSMemoryMonitor)
    mock_mon.get_system_snapshot.return_value = SystemMemorySnapshot(
        direct=direct, derived=derived, timestamp=now
    )
    policy = MemoryPressurePolicy(
        os_safety_reserve_bytes=1_000_000_000,
        min_free_memory_for_load_bytes=1_073_741_824,
    )
    manager = ResourceManager(monitor=mock_mon, estimator=estimator, policy=policy)

    decision = await manager.acquire_admission("llama3.2:3b", "req-reserve-enforced", 2048)
    assert decision.outcome == AdmissionOutcome.REJECT
    assert "below the conservative model-load threshold" in decision.reason.lower()
    assert decision.available_headroom_bytes == 500_000_000


@pytest.mark.asyncio
async def test_darwin_cold_load_rejected_on_unknown_telemetry_or_pressure(estimator):
    """Verify cold model load is rejected conservatively when kernel pressure is UNKNOWN."""
    now = datetime.now(UTC)
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),
        free_bytes=90 * (1024**2),
        pressure_level=MemoryPressureLevel.UNKNOWN,
        is_available=True,
        measured_at=now,
    )
    derived = DerivedHeadroom(
        reclaimable_bytes=0,
        effective_host_headroom_bytes=0,
        os_safety_reserve_bytes=1024**3,
        inactive_reclaim_factor=0.0,
        is_conservative=True,
        computed_at=now,
    )
    mock_mon = MagicMock(spec=MacOSMemoryMonitor)
    mock_mon.get_system_snapshot.return_value = SystemMemorySnapshot(
        direct=direct, derived=derived, timestamp=now
    )
    manager = ResourceManager(monitor=mock_mon, estimator=estimator)
    decision = await manager.acquire_admission("llama3.2:3b", "req-unknown-pressure", 2048)
    assert decision.outcome == AdmissionOutcome.REJECT
    assert "telemetry unavailable" in decision.reason.lower()


@pytest.mark.asyncio
async def test_multiple_cold_load_reservations_deduct_without_double_counting(
    estimator,
):
    """Verify multiple active cold reservations correctly reduce effective headroom without double-counting."""
    # Host has 5.0 GB headroom
    mock_mon = MagicMock(spec=MacOSMemoryMonitor)
    mock_mon.get_system_snapshot.return_value = _make_mock_snapshot(5_000_000_000)
    manager = ResourceManager(monitor=mock_mon, estimator=estimator)

    # First cold model reservation (~2.3 GB)
    dec1 = await manager.acquire_admission("llama3.2:3b", "req-multi-1", 2048)
    assert dec1.outcome == AdmissionOutcome.ALLOW
    assert dec1.handle.pending_load is not None

    # Second request evaluates under active pending reservation of first request
    # Effective headroom should be 5.0 GB - base1 (~2.7 GB remaining)
    dec2 = await manager.acquire_admission("llama3.2:3b", "req-multi-2", 2048)
    assert dec2.outcome == AdmissionOutcome.ALLOW
    assert dec2.handle.pending_load is not None

    # Third request: 5.0 GB - (base1 + base2) < 2.3 GB -> must DEFER while active loads in flight
    dec3 = await manager.acquire_admission("llama3.2:3b", "req-multi-3", 2048)
    assert dec3.outcome == AdmissionOutcome.DEFER
    assert "active leases" in dec3.reason.lower() or "pending loads" in dec3.reason.lower()
