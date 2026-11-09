"""Model memory estimation engine with 4-tier confidence and empirical calibration."""

from app.ai.resources.types import EstimateConfidence, MemoryEstimate
from app.core.logging import logger


class ModelMemoryEstimator:
    """Estimates model memory requirements using GQA formulas and /api/ps calibration."""

    DEFAULT_SCRATCH_BYTES = 50_331_648  # 48 MiB activation scratch buffer
    DEFAULT_RUNTIME_OVERHEAD_BYTES = 67_108_864  # 64 MiB framework and Metal driver overhead
    DEFAULT_FP16_BYTES_PER_KV_ELEMENT = 2.0  # Standard FP16 KV cache in Ollama/llama.cpp

    def __init__(self) -> None:
        # Cache of observed points: model_id -> list of (resident_bytes, context_length)
        self._observations: dict[str, list[tuple[int, int]]] = {}
        # Cached architectural parameters: model_id -> dict
        self._model_arch_cache: dict[str, dict] = {}

    def register_observation(self, model_id: str, resident_bytes: int, context_length: int) -> None:
        """Register an empirical telemetry observation from /api/ps for calibration."""
        if resident_bytes <= 0 or context_length <= 0:
            return

        clean_id = model_id.split("/")[-1]
        obs_list = self._observations.setdefault(clean_id, [])

        # Avoid exact duplicate points
        for r_bytes, ctx in obs_list:
            if ctx == context_length:
                # Check for significant drift (>10%)
                if abs(r_bytes - resident_bytes) / r_bytes > 0.10:
                    logger.info(
                        f"Calibration re-baselined for '{clean_id}': observed {resident_bytes} "
                        f"vs prior {r_bytes} at ctx {context_length}"
                    )
                    obs_list.remove((r_bytes, ctx))
                    break
                return

        obs_list.append((resident_bytes, context_length))
        logger.debug(
            f"Registered empirical memory observation for '{clean_id}': "
            f"{resident_bytes / (1024**2):.1f} MB at context {context_length}"
        )

    def cache_model_architecture(self, model_id: str, arch_metadata: dict) -> None:
        """Cache GGUF architectural metadata extracted during discovery or /api/show."""
        clean_id = model_id.split("/")[-1]
        self._model_arch_cache[clean_id] = arch_metadata

    def compute_kv_cache_bytes(
        self,
        context_length: int,
        n_layers: int,
        n_kv_heads: int,
        head_dim: int,
        bytes_per_element: float = 2.0,
    ) -> int:
        """Calculate exact Key-Value cache memory requirement using Grouped-Query Attention formula.

        Formula: 2 * n_layers * n_kv_heads * head_dim * bytes_per_element * context_length
        (Factor of 2 accounts for Key matrix and Value matrix).
        """
        if context_length <= 0 or n_layers <= 0 or n_kv_heads <= 0 or head_dim <= 0:
            return 0
        bytes_per_token = 2 * n_layers * n_kv_heads * head_dim * bytes_per_element
        return int(context_length * bytes_per_token)

    def estimate_model_memory(
        self,
        model_id: str,
        context_length: int,
        arch_metadata: dict | None = None,
    ) -> MemoryEstimate:
        """Compute multi-tier memory requirement estimate for the given model and context length."""
        clean_id = model_id.split("/")[-1]
        arch = arch_metadata or self._model_arch_cache.get(clean_id, {})
        observations = self._observations.get(clean_id, [])

        # ----------------------------------------------------------------------
        # Tier 1: Exact Observed Match
        # ----------------------------------------------------------------------
        for r_bytes, ctx in observations:
            if ctx == context_length:
                weights_est = int(r_bytes * 0.80)
                kv_est = int(r_bytes * 0.15)
                scratch = int(r_bytes * 0.03)
                overhead = r_bytes - weights_est - kv_est - scratch
                return MemoryEstimate(
                    model_id=clean_id,
                    context_length=context_length,
                    weights_bytes=weights_est,
                    kv_cache_bytes=kv_est,
                    compute_scratch_bytes=scratch,
                    runtime_overhead_bytes=overhead,
                    total_estimated_bytes=r_bytes,
                    confidence=EstimateConfidence.OBSERVED,
                    source="/api/ps live residency",
                    assumptions=["Measured directly from resident Ollama process on this host"],
                )

        # ----------------------------------------------------------------------
        # Extract GGUF architecture fields if available
        # ----------------------------------------------------------------------
        model_info = arch.get("model_info", {})
        arch_prefix = model_info.get("general.architecture", "llama")

        n_layers = model_info.get(f"{arch_prefix}.block_count")
        n_kv_heads = model_info.get(f"{arch_prefix}.attention.head_count_kv")
        head_dim = model_info.get(f"{arch_prefix}.attention.key_length")
        file_size = arch.get("size") or model_info.get("general.size") or 0

        # KV-cache precision assumption
        b_kv = self.DEFAULT_FP16_BYTES_PER_KV_ELEMENT
        kv_assumptions = [
            "Assumes standard FP16 (2.0 bytes/element) KV cache precision in Ollama runner"
        ]

        # Calculate KV cache for requested context if architecture is known
        has_gqa_params = n_layers and n_kv_heads and head_dim
        kv_bytes_for_requested = 0
        if has_gqa_params:
            kv_bytes_for_requested = self.compute_kv_cache_bytes(
                context_length=context_length,
                n_layers=n_layers,
                n_kv_heads=n_kv_heads,
                head_dim=head_dim,
                bytes_per_element=b_kv,
            )

        # ----------------------------------------------------------------------
        # Tier 2: Empirically Calibrated Extrapolation
        # ----------------------------------------------------------------------
        if observations and has_gqa_params:
            # Use most recent observation point to deduce M_base
            obs_bytes, obs_ctx = observations[-1]
            obs_kv = self.compute_kv_cache_bytes(
                context_length=obs_ctx,
                n_layers=n_layers,
                n_kv_heads=n_kv_heads,
                head_dim=head_dim,
                bytes_per_element=b_kv,
            )
            base_memory = max(0, obs_bytes - obs_kv)
            calibrated_total = base_memory + kv_bytes_for_requested
            confidence = (
                EstimateConfidence.CALIBRATED_MULTI_POINT
                if len(observations) >= 2
                else EstimateConfidence.CALIBRATED_SINGLE_POINT
            )

            weights_est = int(base_memory * 0.90)
            overhead_est = base_memory - weights_est

            return MemoryEstimate(
                model_id=clean_id,
                context_length=context_length,
                weights_bytes=weights_est,
                kv_cache_bytes=kv_bytes_for_requested,
                compute_scratch_bytes=self.DEFAULT_SCRATCH_BYTES,
                runtime_overhead_bytes=overhead_est,
                total_estimated_bytes=calibrated_total,
                confidence=confidence,
                source="Ollama /api/ps calibrated extrapolation",
                assumptions=[
                    (
                        f"Calibrated from observed point ({obs_bytes / (1024**2):.1f} MB "
                        f"at ctx {obs_ctx})"
                    ),
                    "Base weights and Metal overhead assumed constant across contexts",
                    *kv_assumptions,
                ],
            )

        # ----------------------------------------------------------------------
        # Tier 3: Theoretical Formulation from GGUF Metadata
        # ----------------------------------------------------------------------
        if has_gqa_params and file_size:
            weights_bytes = file_size
            scratch_bytes = self.DEFAULT_SCRATCH_BYTES
            overhead_bytes = self.DEFAULT_RUNTIME_OVERHEAD_BYTES
            total = weights_bytes + kv_bytes_for_requested + scratch_bytes + overhead_bytes

            return MemoryEstimate(
                model_id=clean_id,
                context_length=context_length,
                weights_bytes=weights_bytes,
                kv_cache_bytes=kv_bytes_for_requested,
                compute_scratch_bytes=scratch_bytes,
                runtime_overhead_bytes=overhead_bytes,
                total_estimated_bytes=total,
                confidence=EstimateConfidence.THEORETICAL,
                source="GGUF tensor architecture metadata",
                assumptions=[
                    f"Architecture: {n_layers} layers, {n_kv_heads} KV heads, {head_dim} head dim",
                    "Model weights mapped directly from GGUF file size",
                    f"Activation scratch bounded at {scratch_bytes / (1024**2):.1f} MiB",
                    *kv_assumptions,
                ],
            )

        # ----------------------------------------------------------------------
        # Tier 4: Heuristic Fallback
        # ----------------------------------------------------------------------
        # Base weight estimate from file_size or parameter count
        param_size_str = arch.get("details", {}).get("parameter_size") or "3B"
        param_billions = 3.0
        try:
            if "b" in param_size_str.lower():
                param_billions = float(param_size_str.lower().replace("b", "").strip())
        except Exception:
            pass

        fallback_weights = int(file_size or (param_billions * 1.8 * (1024**3)))
        # Conservative heuristic KV cache: 100 KB per token for 3B, scaling linearly
        fallback_kv = int(context_length * (param_billions / 3.0) * 102_400)
        fallback_total = (
            fallback_weights
            + fallback_kv
            + self.DEFAULT_SCRATCH_BYTES
            + self.DEFAULT_RUNTIME_OVERHEAD_BYTES
        )

        return MemoryEstimate(
            model_id=clean_id,
            context_length=context_length,
            weights_bytes=fallback_weights,
            kv_cache_bytes=fallback_kv,
            compute_scratch_bytes=self.DEFAULT_SCRATCH_BYTES,
            runtime_overhead_bytes=self.DEFAULT_RUNTIME_OVERHEAD_BYTES,
            total_estimated_bytes=fallback_total,
            confidence=EstimateConfidence.HEURISTIC,
            source="Parameter size scaling heuristic",
            assumptions=[
                "Incomplete or unavailable GGUF tensor architecture metadata",
                "Weight size estimated from file size or parameter label",
                "KV cache estimated via linear parameter scaling with +-35% uncertainty",
            ],
        )
