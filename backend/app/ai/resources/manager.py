"""Resource manager coordinating memory budgeting, atomic admissions, and lifecycle safeguards."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from app.ai.resources.estimator import ModelMemoryEstimator
from app.ai.resources.monitor import MacOSMemoryMonitor
from app.ai.resources.types import (
    AdmissionDecision,
    AdmissionOutcome,
    InferenceAllocation,
    MemoryPressureLevel,
    MemoryPressurePolicy,
    PendingModelLoadReservation,
    ReservationHandle,
    ResidentModelAllocation,
    SystemMemorySnapshot,
)
from app.core.logging import logger


class ResourceManager:
    """Coordinates memory monitoring, model estimation, and admission control."""

    def __init__(
        self,
        monitor: MacOSMemoryMonitor | None = None,
        estimator: ModelMemoryEstimator | None = None,
        policy: MemoryPressurePolicy | None = None,
    ) -> None:
        self.monitor = monitor or MacOSMemoryMonitor()
        self.estimator = estimator or ModelMemoryEstimator()
        self.policy = policy or MemoryPressurePolicy()

        # Concurrency synchronization lock (protects internal state tables)
        # NEVER held across network I/O, subprocess calls, or provider requests.
        self._lock = asyncio.Lock()

        # Layer A: Persistent memory committed to loaded models in inference daemon
        # Key: clean model_id -> ResidentModelAllocation
        self._resident_models: dict[str, ResidentModelAllocation] = {}

        # Layer B: Ephemeral memory allocations dedicated to in-flight inference requests
        # Key: request_id -> InferenceAllocation
        self._active_inferences: dict[str, InferenceAllocation] = {}

        # Provisional reservations protecting uncommitted memory during model loading
        # Key: reservation_id -> PendingModelLoadReservation
        self._pending_loads: dict[str, PendingModelLoadReservation] = {}

        # Models currently undergoing two-phase unload
        self._evicting_models: set[str] = set()

        # Anti-thrashing history: clean model_id -> timestamp of last unload
        self._unload_history: dict[str, datetime] = {}

        # Cached host snapshot for high-frequency checks
        self._cached_snapshot: SystemMemorySnapshot | None = None
        self._cached_snapshot_time: datetime | None = None

        # Background observation and reconciliation task
        self._background_task: asyncio.Task | None = None
        self._is_running = False

    # --------------------------------------------------------------------------
    # Telemetry Sampling (Executed OUTSIDE the lock)
    # --------------------------------------------------------------------------

    def get_fresh_host_snapshot(self) -> SystemMemorySnapshot:
        """Sample fresh host memory snapshot directly from the kernel outside lock."""
        snapshot = self.monitor.get_system_snapshot(self.policy)
        self._cached_snapshot = snapshot
        self._cached_snapshot_time = snapshot.timestamp
        return snapshot

    def _get_snapshot(self) -> SystemMemorySnapshot:
        """Return cached snapshot if fresh (< freshness_seconds), otherwise sample fresh."""
        now = datetime.now(UTC)
        if (
            self._cached_snapshot is not None
            and self._cached_snapshot_time is not None
            and (now - self._cached_snapshot_time).total_seconds()
            < self.policy.snapshot_freshness_seconds
        ):
            return self._cached_snapshot
        return self.get_fresh_host_snapshot()

    # --------------------------------------------------------------------------
    # Core Accounting Invariants
    # --------------------------------------------------------------------------

    def _calculate_effective_headroom(self, snapshot: SystemMemorySnapshot) -> int:
        """Compute effective headroom available for new allocations under lock.

        Invariant: Host snapshot already accounts for resident models in Mach counters.
        Only uncommitted pending load reservations are subtracted.
        """
        host_headroom = snapshot.derived.effective_host_headroom_bytes
        pending_total = sum(
            p.reserved_base_bytes for p in self._pending_loads.values() if p.is_active
        )
        return max(0, host_headroom - pending_total)

    def _clean_id(self, model_id: str) -> str:
        """Normalize model identifier (e.g. 'ollama/llama3.2:3b' -> 'llama3.2:3b')."""
        return model_id.split("/")[-1].strip()

    # --------------------------------------------------------------------------
    # Atomic Admission & Reservation Evaluation
    # --------------------------------------------------------------------------

    async def evaluate_admission(
        self,
        model_id: str,
        requested_context: int | None = None,
        arch_metadata: dict | None = None,
    ) -> AdmissionDecision:
        """Dry-run diagnostic evaluation of admission without reserving budget."""
        snapshot = self.get_fresh_host_snapshot()
        async with self._lock:
            return self._evaluate_admission_under_lock(
                model_id=model_id,
                requested_context=requested_context,
                snapshot=snapshot,
                arch_metadata=arch_metadata,
                acquire=False,
                request_id=None,
            )

    async def acquire_admission(
        self,
        model_id: str,
        request_id: str,
        requested_context: int | None = None,
        arch_metadata: dict | None = None,
    ) -> AdmissionDecision:
        """Atomically evaluate admission and commit reservations in a single critical section."""
        # 1. Obtain fresh host telemetry OUTSIDE the lock
        snapshot = self.get_fresh_host_snapshot()

        # 2. Atomic evaluation and commitment INSIDE the lock
        async with self._lock:
            return self._evaluate_admission_under_lock(
                model_id=model_id,
                requested_context=requested_context,
                snapshot=snapshot,
                arch_metadata=arch_metadata,
                acquire=True,
                request_id=request_id,
            )

    def _evaluate_admission_under_lock(
        self,
        model_id: str,
        requested_context: int | None,
        snapshot: SystemMemorySnapshot,
        arch_metadata: dict | None,
        acquire: bool,
        request_id: str | None,
    ) -> AdmissionDecision:
        """Internal critical section evaluating headroom and committing reservations."""
        clean_id = self._clean_id(model_id)
        now = datetime.now(UTC)

        # 1. Check critical pressure state
        if snapshot.direct.pressure_level == MemoryPressureLevel.CRITICAL:
            return AdmissionDecision(
                outcome=AdmissionOutcome.REJECT,
                target_model_id=clean_id,
                requested_context=requested_context or self.policy.default_context_length,
                allocated_context=0,
                estimated_total_bytes=0,
                available_headroom_bytes=0,
                reason="Admission blocked: operating system is under critical memory pressure",
                evaluated_at=now,
            )

        # 2. Determine target context window length
        req_ctx = (
            requested_context
            if (requested_context is not None and requested_context > 0)
            else self.policy.default_context_length
        )
        is_pressure_constrained = (
            snapshot.direct.pressure_level == MemoryPressureLevel.WARNING
            or snapshot.direct.pressure_level == MemoryPressureLevel.UNKNOWN
            or not snapshot.direct.is_available
        )
        target_ctx = (
            min(req_ctx, self.policy.reduced_context_length) if is_pressure_constrained else req_ctx
        )

        # 3. Check memory requirements
        is_resident = clean_id in self._resident_models
        resident_alloc = self._resident_models.get(clean_id)

        estimate = self.estimator.estimate_model_memory(clean_id, target_ctx, arch_metadata)
        effective_headroom = self._calculate_effective_headroom(snapshot)

        # Determine incremental memory needed for this request
        if is_resident and resident_alloc is not None:
            # Base weights and loaded KV cache already accounted for in host memory.
            if target_ctx <= resident_alloc.verified_context_length:
                # KV cache already resident; only activation scratch needed
                incremental_needed = estimate.compute_scratch_bytes
            else:
                # Incremental delta KV cache for expanded context
                delta_ctx = target_ctx - resident_alloc.verified_context_length
                model_info = arch_metadata.get("model_info", {}) if arch_metadata else {}
                delta_kv = self.estimator.compute_kv_cache_bytes(
                    context_length=delta_ctx,
                    n_layers=model_info.get(f"{clean_id}.block_count", 28),
                    n_kv_heads=model_info.get(f"{clean_id}.attention.head_count_kv", 8),
                    head_dim=model_info.get(f"{clean_id}.attention.key_length", 128),
                )
                incremental_needed = delta_kv + estimate.compute_scratch_bytes
            pending_base_needed = 0
        else:
            # Model not resident: must reserve base model footprint + scratch
            incremental_needed = estimate.compute_scratch_bytes
            pending_base_needed = (
                estimate.weights_bytes + estimate.kv_cache_bytes + estimate.runtime_overhead_bytes
            )

        total_req_bytes = pending_base_needed + incremental_needed

        # 4. Conservative model-load verification: require minimum verified available headroom
        if not is_resident:
            req_free_bytes = self.policy.min_free_memory_for_load_bytes
            req_free_mb = req_free_bytes / (1024**2)

            if (
                not snapshot.direct.is_available
                or snapshot.direct.free_bytes is None
                or snapshot.derived.is_conservative
            ):
                diag = (
                    f": {snapshot.direct.diagnostic_message}"
                    if snapshot.direct.diagnostic_message
                    else ""
                )
                return AdmissionDecision(
                    outcome=AdmissionOutcome.REJECT,
                    target_model_id=clean_id,
                    requested_context=req_ctx,
                    allocated_context=0,
                    estimated_total_bytes=total_req_bytes,
                    available_headroom_bytes=effective_headroom,
                    reason=(
                        f"Admission rejected: required memory telemetry unavailable; "
                        f"cannot verify minimum {req_free_mb:.1f} MB free memory "
                        f"for model load{diag}"
                    ),
                    evaluated_at=now,
                )

            if effective_headroom < req_free_bytes:
                avail_mb = effective_headroom / (1024**2)

                # Identify an eligible idle resident model as a provisional eviction candidate
                # No speculative memory credit is added to free memory or effective headroom.
                eviction_candidate = self._find_eviction_candidate_under_lock(
                    target_clean_id=clean_id, now=now
                )
                if eviction_candidate:
                    req_mb = total_req_bytes / (1024**2)
                    return AdmissionDecision(
                        outcome=AdmissionOutcome.REQUIRE_UNLOAD,
                        target_model_id=clean_id,
                        requested_context=req_ctx,
                        allocated_context=target_ctx,
                        estimated_total_bytes=total_req_bytes,
                        available_headroom_bytes=effective_headroom,
                        models_to_unload=[eviction_candidate],
                        reason=(
                            f"Verified available headroom ({avail_mb:.1f} MB) is below "
                            f"{req_free_mb:.1f} MB threshold; "
                            f"requires provisional eviction of '{eviction_candidate}'"
                        ),
                        evaluated_at=now,
                    )

                # Check if active leases or pending loads are in flight (temporary deferral)
                if (
                    any(p.is_active for p in self._pending_loads.values())
                    or self._active_inferences
                ):
                    return AdmissionDecision(
                        outcome=AdmissionOutcome.DEFER,
                        target_model_id=clean_id,
                        requested_context=req_ctx,
                        allocated_context=0,
                        estimated_total_bytes=total_req_bytes,
                        available_headroom_bytes=effective_headroom,
                        reason=(
                            f"Verified available headroom ({avail_mb:.1f} MB) is below threshold "
                            f"({req_free_mb:.1f} MB); deferring while active leases finish"
                        ),
                        evaluated_at=now,
                    )

                return AdmissionDecision(
                    outcome=AdmissionOutcome.REJECT,
                    target_model_id=clean_id,
                    requested_context=req_ctx,
                    allocated_context=0,
                    estimated_total_bytes=total_req_bytes,
                    available_headroom_bytes=effective_headroom,
                    reason=(
                        f"Admission rejected: verified available headroom ({avail_mb:.1f} MB) "
                        f"is below the conservative model-load threshold ({req_free_mb:.1f} MB)"
                    ),
                    evaluated_at=now,
                )

        # 5. Evaluate fit against effective headroom
        if effective_headroom >= total_req_bytes:
            outcome = (
                AdmissionOutcome.ALLOW_WITH_REDUCED_CONTEXT
                if target_ctx < req_ctx
                else AdmissionOutcome.ALLOW
            )
            handle = None
            if acquire and request_id:
                handle = self._commit_reservation_under_lock(
                    clean_id=clean_id,
                    request_id=request_id,
                    target_ctx=target_ctx,
                    pending_base_bytes=pending_base_needed,
                    incremental_bytes=incremental_needed,
                    now=now,
                )

            req_mb = total_req_bytes / (1024**2)
            head_mb = effective_headroom / (1024**2)
            if outcome == AdmissionOutcome.ALLOW_WITH_REDUCED_CONTEXT:
                if (
                    not snapshot.direct.is_available
                    or snapshot.direct.pressure_level == MemoryPressureLevel.UNKNOWN
                ):
                    diag = (
                        f": {snapshot.direct.diagnostic_message}"
                        if snapshot.direct.diagnostic_message
                        else ""
                    )
                    reason = (
                        f"Admitted with conservative reduced context ({target_ctx} tokens) "
                        f"due to degraded/unobservable host telemetry{diag}"
                    )
                else:
                    reason = (
                        f"Admitted with reduced context ({target_ctx} tokens) "
                        "due to memory constraints"
                    )
            else:
                reason = (
                    f"Admitted ({target_ctx} tokens); required {req_mb:.1f} MB, "
                    f"headroom {head_mb:.1f} MB"
                )

            return AdmissionDecision(
                outcome=outcome,
                target_model_id=clean_id,
                requested_context=req_ctx,
                allocated_context=target_ctx,
                estimated_total_bytes=total_req_bytes,
                available_headroom_bytes=effective_headroom,
                handle=handle,
                reason=reason,
                evaluated_at=now,
            )

        # 6. Headroom insufficient at target_ctx; attempt reduced context if possible
        if target_ctx > self.policy.min_context_length:
            reduced_ctx = max(self.policy.min_context_length, self.policy.reduced_context_length)
            if reduced_ctx < target_ctx:
                reduced_estimate = self.estimator.estimate_model_memory(
                    clean_id, reduced_ctx, arch_metadata
                )
                reduced_base = (
                    0
                    if is_resident
                    else (
                        reduced_estimate.weights_bytes
                        + reduced_estimate.kv_cache_bytes
                        + reduced_estimate.runtime_overhead_bytes
                    )
                )
                reduced_total = reduced_base + reduced_estimate.compute_scratch_bytes
                if effective_headroom >= reduced_total:
                    handle = None
                    if acquire and request_id:
                        handle = self._commit_reservation_under_lock(
                            clean_id=clean_id,
                            request_id=request_id,
                            target_ctx=reduced_ctx,
                            pending_base_bytes=reduced_base,
                            incremental_bytes=reduced_estimate.compute_scratch_bytes,
                            now=now,
                        )
                    return AdmissionDecision(
                        outcome=AdmissionOutcome.ALLOW_WITH_REDUCED_CONTEXT,
                        target_model_id=clean_id,
                        requested_context=req_ctx,
                        allocated_context=reduced_ctx,
                        estimated_total_bytes=reduced_total,
                        available_headroom_bytes=effective_headroom,
                        handle=handle,
                        reason=(
                            f"Admitted with clamped context ({reduced_ctx} tokens) to fit headroom"
                        ),
                        evaluated_at=now,
                    )

        # 7. Cannot fit without evicting an idle resident model; identify candidate
        # No speculative memory credit is added to effective headroom.
        eviction_candidate = self._find_eviction_candidate_under_lock(
            target_clean_id=clean_id, now=now
        )
        if eviction_candidate:
            req_mb = total_req_bytes / (1024**2)
            head_mb = effective_headroom / (1024**2)
            return AdmissionDecision(
                outcome=AdmissionOutcome.REQUIRE_UNLOAD,
                target_model_id=clean_id,
                requested_context=req_ctx,
                allocated_context=target_ctx,
                estimated_total_bytes=total_req_bytes,
                available_headroom_bytes=effective_headroom,
                models_to_unload=[eviction_candidate],
                reason=(
                    f"Requires {req_mb:.1f} MB exceeding headroom ({head_mb:.1f} MB); "
                    f"requires provisional eviction of '{eviction_candidate}'"
                ),
                evaluated_at=now,
            )

        # 8. Check if active leases or pending unloads make this a temporary deferral
        if any(p.is_active for p in self._pending_loads.values()) or self._active_inferences:
            return AdmissionDecision(
                outcome=AdmissionOutcome.DEFER,
                target_model_id=clean_id,
                requested_context=req_ctx,
                allocated_context=0,
                estimated_total_bytes=total_req_bytes,
                available_headroom_bytes=effective_headroom,
                reason="Insufficient headroom currently; active leases or pending loads in flight",
                evaluated_at=now,
            )

        # 9. Unrecoverable: budget exhausted
        req_mb = total_req_bytes / (1024**2)
        head_mb = effective_headroom / (1024**2)
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECT,
            target_model_id=clean_id,
            requested_context=req_ctx,
            allocated_context=0,
            estimated_total_bytes=total_req_bytes,
            available_headroom_bytes=effective_headroom,
            reason=f"Insufficient memory: {req_mb:.1f} MB exceeds headroom {head_mb:.1f} MB",
            evaluated_at=now,
        )

    def _commit_reservation_under_lock(
        self,
        clean_id: str,
        request_id: str,
        target_ctx: int,
        pending_base_bytes: int,
        incremental_bytes: int,
        now: datetime,
    ) -> ReservationHandle:
        """Atomically commit reservation records into state tables under lock."""
        pending_res = None
        if pending_base_bytes > 0:
            expires_at = datetime.fromtimestamp(
                now.timestamp() + self.policy.reservation_timeout_seconds,
                tz=UTC,
            )
            pending_res = PendingModelLoadReservation(
                model_id=clean_id,
                reserved_base_bytes=pending_base_bytes,
                created_at=now,
                expires_at=expires_at,
                requesting_request_id=request_id,
                is_active=True,
            )
            self._pending_loads[pending_res.reservation_id] = pending_res

        inference_alloc = InferenceAllocation(
            request_id=request_id,
            model_id=clean_id,
            allocated_context_length=target_ctx,
            inference_overhead_bytes=incremental_bytes,
            created_at=now,
        )
        self._active_inferences[request_id] = inference_alloc

        return ReservationHandle(pending_load=pending_res, inference=inference_alloc)

    def _find_eviction_candidate_under_lock(
        self, target_clean_id: str, now: datetime
    ) -> str | None:
        """Identify an idle resident model eligible for eviction without race conditions."""
        active_model_ids = {inf.model_id for inf in self._active_inferences.values()}
        pending_model_ids = {p.model_id for p in self._pending_loads.values() if p.is_active}

        for resident_id in self._resident_models:
            if resident_id == target_clean_id:
                continue
            if resident_id in active_model_ids:
                continue
            if resident_id in pending_model_ids:
                continue
            if resident_id in self._evicting_models:
                continue

            # Check cooldown against thrashing
            last_unload = self._unload_history.get(resident_id)
            if (
                last_unload is not None
                and (now - last_unload).total_seconds() < self.policy.unload_cooldown_seconds
            ):
                continue

            return resident_id

        return None

    # --------------------------------------------------------------------------
    # Two-Phase Eviction Execution (I/O OUTSIDE lock, revalidation INSIDE lock)
    # --------------------------------------------------------------------------

    async def execute_two_phase_admission(
        self,
        model_id: str,
        request_id: str,
        requested_context: int | None,
        provider_unload_callable,  # Async callable(model_id) -> bool
        arch_metadata: dict | None = None,
    ) -> AdmissionDecision:
        """Execute atomic admission with safe out-of-lock eviction and post-unload revalidation."""
        # Phase 1: Initial atomic admission attempt and mark eviction candidate under lock
        snapshot_initial = self.get_fresh_host_snapshot()
        async with self._lock:
            decision = self._evaluate_admission_under_lock(
                model_id=model_id,
                requested_context=requested_context,
                snapshot=snapshot_initial,
                arch_metadata=arch_metadata,
                acquire=True,
                request_id=request_id,
            )
            if decision.outcome != AdmissionOutcome.REQUIRE_UNLOAD or not decision.models_to_unload:
                return decision

            candidate_id = decision.models_to_unload[0]
            # Mark candidate as evicting under lock so concurrent requests cannot consume it
            self._evicting_models.add(candidate_id)

        unload_time = datetime.now(UTC)
        try:
            # Phase 2: Execute unload OUTSIDE lock
            logger.info(f"Two-phase admission: unloading model '{candidate_id}' outside lock...")
            unload_success = await provider_unload_callable(candidate_id)
            now = datetime.now(UTC)
            self._unload_history[candidate_id] = now

            if not unload_success:
                logger.warning(f"Unload of model '{candidate_id}' failed or reported false")
                return AdmissionDecision(
                    outcome=AdmissionOutcome.DEFER,
                    target_model_id=self._clean_id(model_id),
                    requested_context=decision.requested_context,
                    allocated_context=0,
                    estimated_total_bytes=decision.estimated_total_bytes,
                    available_headroom_bytes=decision.available_headroom_bytes,
                    reason=f"Eviction of model '{candidate_id}' failed; admission deferred",
                    evaluated_at=now,
                )

            # Phase 3: Sample fresh host telemetry OUTSIDE lock
            snapshot_post = self.get_fresh_host_snapshot()
            now_post = datetime.now(UTC)

            # Validate that post-unload telemetry is available and genuinely fresh
            is_stale = (
                snapshot_post.timestamp < unload_time
                or (now_post - snapshot_post.timestamp).total_seconds()
                > self.policy.snapshot_freshness_seconds
            )
            is_unavailable = (
                not snapshot_post.direct.is_available
                or snapshot_post.direct.free_bytes is None
                or snapshot_post.derived.is_conservative
            )

            if is_unavailable or is_stale:
                diag = (
                    f": {snapshot_post.direct.diagnostic_message}"
                    if snapshot_post.direct.diagnostic_message
                    else ""
                )
                reason = (
                    f"Post-unload telemetry unavailable{diag}; admission rejected conservatively"
                    if is_unavailable
                    else (
                        f"Post-unload telemetry is stale (timestamp "
                        f"{snapshot_post.timestamp.isoformat()} precedes unload at "
                        f"{unload_time.isoformat()}); admission rejected conservatively"
                    )
                )
                logger.warning(reason)
                return AdmissionDecision(
                    outcome=AdmissionOutcome.REJECT,
                    target_model_id=self._clean_id(model_id),
                    requested_context=decision.requested_context,
                    allocated_context=0,
                    estimated_total_bytes=decision.estimated_total_bytes,
                    available_headroom_bytes=0,
                    reason=reason,
                    evaluated_at=now_post,
                )

            # Phase 4: Re-enter lock, clear Layer A for candidate, and revalidate admission
            async with self._lock:
                self._resident_models.pop(candidate_id, None)
                # Re-evaluate admission with fresh post-unload telemetry
                final_decision = self._evaluate_admission_under_lock(
                    model_id=model_id,
                    requested_context=requested_context,
                    snapshot=snapshot_post,
                    arch_metadata=arch_metadata,
                    acquire=True,
                    request_id=request_id,
                )
                return final_decision
        finally:
            async with self._lock:
                self._evicting_models.discard(candidate_id)

    # --------------------------------------------------------------------------
    # Lifecycle Handover & Reconciliation
    # --------------------------------------------------------------------------

    async def confirm_model_loaded(
        self,
        model_id: str,
        verified_context: int,
        loaded_timestamp: datetime | None = None,
        observed_size_bytes: int | None = None,
    ) -> None:
        """Retire pending load reservation into Layer A resident allocation."""
        clean_id = self._clean_id(model_id)
        now = loaded_timestamp or datetime.now(UTC)

        async with self._lock:
            # Calculate base memory
            base_bytes = observed_size_bytes or 0
            # Find and deactivate matching pending reservations
            matching_pendings = [p for p in self._pending_loads.values() if p.model_id == clean_id]
            for p in matching_pendings:
                p.is_active = False
                if not base_bytes:
                    base_bytes = p.reserved_base_bytes
                self._pending_loads.pop(p.reservation_id, None)

            # Record Layer A persistent allocation
            self._resident_models[clean_id] = ResidentModelAllocation(
                model_id=clean_id,
                base_memory_bytes=base_bytes,
                verified_context_length=verified_context,
                loaded_at=now,
                is_external=False,
                source="confirmed_load",
            )
            logger.info(
                f"Model '{clean_id}' confirmed resident: {base_bytes / (1024**2):.1f} MB "
                f"at context {verified_context}"
            )

    async def release_inference(self, request_id: str) -> None:
        """Release Layer B ephemeral inference allocation; Layer A resident allocation is RETAINED.

        Pending load reservations are preserved to prevent caller cancellation from releasing
        memory budget while the underlying model load may still be executing in the daemon.
        Pending reservations are retired upon confirmation or expire via TTL sweep.
        """
        async with self._lock:
            removed = self._active_inferences.pop(request_id, None)
            if removed:
                logger.debug(
                    f"Released Layer B inference allocation for request '{request_id}' "
                    f"({removed.inference_overhead_bytes / (1024**2):.1f} MB)"
                )

    async def cancel_pending_reservation(self, request_id: str) -> None:
        """Explicitly cancel and discard a pending reservation when load definitely aborted."""
        async with self._lock:
            unconfirmed_ids = [
                rid
                for rid, p in self._pending_loads.items()
                if p.requesting_request_id == request_id
            ]
            for rid in unconfirmed_ids:
                p = self._pending_loads.pop(rid, None)
                if p:
                    logger.debug(
                        f"Cancelled pending load reservation '{rid}' for model '{p.model_id}'"
                    )

    async def sweep_expired_reservations(self, now: datetime | None = None) -> list[str]:
        """Sweep and retire expired pending load reservations whose TTL has elapsed."""
        sweep_time = now or datetime.now(UTC)
        swept = []
        async with self._lock:
            expired_ids = [
                rid
                for rid, p in self._pending_loads.items()
                if p.is_active and p.expires_at < sweep_time
            ]
            for rid in expired_ids:
                p = self._pending_loads.pop(rid, None)
                if p:
                    swept.append(rid)
                    logger.warning(
                        f"Swept expired pending load reservation '{rid}' for model '{p.model_id}'"
                    )
        return swept

    async def clear_model_resident(self, model_id: str) -> None:
        """Clear Layer A resident allocation when verified evicted from daemon."""
        clean_id = self._clean_id(model_id)
        async with self._lock:
            removed = self._resident_models.pop(clean_id, None)
            if removed:
                logger.info(
                    f"Cleared Layer A resident allocation for '{clean_id}' "
                    f"({removed.base_memory_bytes / (1024**2):.1f} MB freed from budget)"
                )

    async def reconcile_with_daemon(self, resident_models_from_ps: list[dict]) -> None:
        """Reconcile Layer A with daemon reality outside/inside lock pattern."""
        ps_models: dict[str, dict] = {}
        for item in resident_models_from_ps:
            name = item.get("model") or item.get("name")
            if name:
                ps_models[self._clean_id(name)] = item

        now = datetime.now(UTC)

        async with self._lock:
            # 1. External loads: in /api/ps but not in Layer A
            for clean_id, item in ps_models.items():
                if clean_id not in self._resident_models:
                    vram = item.get("size_vram") or item.get("size") or 0
                    ctx = item.get("context_length") or self.policy.default_context_length
                    self._resident_models[clean_id] = ResidentModelAllocation(
                        model_id=clean_id,
                        base_memory_bytes=vram,
                        verified_context_length=ctx,
                        loaded_at=now,
                        is_external=True,
                        source="daemon_reconciliation",
                    )
                    # Register empirical calibration point
                    self.estimator.register_observation(clean_id, vram, ctx)
                    mb = vram / (1024**2)
                    logger.info(f"Reconciled external model load '{clean_id}': {mb:.1f} MB")

            # 2. External unloads: in Layer A but not in /api/ps
            active_model_ids = {inf.model_id for inf in self._active_inferences.values()}
            pending_model_ids = {p.model_id for p in self._pending_loads.values() if p.is_active}

            stale_models = [
                mid
                for mid in self._resident_models
                if mid not in ps_models
                and mid not in active_model_ids
                and mid not in pending_model_ids
            ]
            for mid in stale_models:
                self._resident_models.pop(mid, None)
                logger.info(f"Reconciled external model eviction for '{mid}'")

    # --------------------------------------------------------------------------
    # Context Manager Convenience Wrapper
    # --------------------------------------------------------------------------

    @asynccontextmanager
    async def reserve_inference(
        self,
        model_id: str,
        request_id: str,
        requested_context: int | None = None,
        provider_unload_callable=None,
        arch_metadata: dict | None = None,
    ) -> AsyncIterator[AdmissionDecision]:
        """Async context manager acquiring admission atomically and guaranteeing Layer B cleanup."""
        if provider_unload_callable:
            decision = await self.execute_two_phase_admission(
                model_id=model_id,
                request_id=request_id,
                requested_context=requested_context,
                provider_unload_callable=provider_unload_callable,
                arch_metadata=arch_metadata,
            )
        else:
            decision = await self.acquire_admission(
                model_id=model_id,
                request_id=request_id,
                requested_context=requested_context,
                arch_metadata=arch_metadata,
            )

        try:
            yield decision
        finally:
            # Layer B is ALWAYS released when stream completes or raises
            await self.release_inference(request_id)

    # --------------------------------------------------------------------------
    # Background Observer Lifecycle
    # --------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background telemetry sampling and reconciliation task."""
        if self._is_running:
            return
        self._is_running = True
        self._background_task = asyncio.create_task(self._background_loop())
        logger.info("ResourceManager background observer started")

    async def stop(self) -> None:
        """Stop background task, clean up reservations, and close monitor."""
        self._is_running = False
        if self._background_task:
            self._background_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._background_task
            self._background_task = None
        self.monitor.close()
        logger.info("ResourceManager stopped cleanly")

    async def _background_loop(self) -> None:
        """Periodic background telemetry update and reservation sweep loop."""
        while self._is_running:
            try:
                # 1. Sample fresh host telemetry outside lock
                self.get_fresh_host_snapshot()

                # 2. Sweep expired pending reservations
                await self.sweep_expired_reservations()

                await asyncio.sleep(self.policy.pressure_poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in ResourceManager background loop: {exc}")
                await asyncio.sleep(self.policy.pressure_poll_interval_seconds)
