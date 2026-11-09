"""Deterministic unit tests for model memory estimation engine."""

from app.ai.resources.estimator import ModelMemoryEstimator
from app.ai.resources.types import EstimateConfidence


def test_gqa_kv_cache_calculation():
    """Verify exact GQA Key-Value cache computation formula."""
    estimator = ModelMemoryEstimator()

    # Llama 3.2 3B architecture parameters:
    # 28 layers, 8 KV heads, 128 head dim, FP16 (2.0 bytes/element)
    # Per-token KV footprint = 2 * 28 * 8 * 128 * 2.0 = 114,688 bytes
    per_token_bytes = 2 * 28 * 8 * 128 * 2.0
    assert per_token_bytes == 114688

    # Context 2048: 2048 * 114,688 = 234,881,024 bytes (224 MiB)
    kv_2048 = estimator.compute_kv_cache_bytes(
        context_length=2048,
        n_layers=28,
        n_kv_heads=8,
        head_dim=128,
        bytes_per_element=2.0,
    )
    assert kv_2048 == 234881024

    # Context 4096: 4096 * 114,688 = 469,762,048 bytes (448 MiB)
    kv_4096 = estimator.compute_kv_cache_bytes(
        context_length=4096,
        n_layers=28,
        n_kv_heads=8,
        head_dim=128,
        bytes_per_element=2.0,
    )
    assert kv_4096 == 469762048
    assert kv_4096 == 2 * kv_2048

    # Quantized KV cache: 4-bit (0.5 bytes per element)
    kv_q4 = estimator.compute_kv_cache_bytes(
        context_length=2048,
        n_layers=28,
        n_kv_heads=8,
        head_dim=128,
        bytes_per_element=0.5,
    )
    assert kv_q4 == kv_2048 // 4


def test_tier_1_observed_exact_match():
    """Verify Tier 1 OBSERVED estimate is produced when exact context was observed."""
    estimator = ModelMemoryEstimator()
    obs_bytes = 2_700_000_000  # 2.7 GB observed
    obs_ctx = 2048

    estimator.register_observation("llama3.2:3b", resident_bytes=obs_bytes, context_length=obs_ctx)

    est = estimator.estimate_model_memory("llama3.2:3b", context_length=2048)
    assert est.confidence == EstimateConfidence.OBSERVED
    assert est.total_estimated_bytes == obs_bytes
    assert est.context_length == 2048
    assert "resident Ollama process" in est.assumptions[0]


def test_tier_2_calibrated_extrapolation():
    """Verify Tier 2 CALIBRATED estimate deduces base weights and scales KV cache."""
    estimator = ModelMemoryEstimator()
    # Cache Llama 3.2 3B arch metadata
    arch = {
        "model_info": {
            "general.architecture": "llama",
            "llama.block_count": 28,
            "llama.attention.head_count_kv": 8,
            "llama.attention.key_length": 128,
            "general.size": 2_000_000_000,
        }
    }
    estimator.cache_model_architecture("llama3.2:3b", arch)

    # Register observation at ctx 2048 (approx 2.49 GB)
    kv_2048 = 234881024
    base_resident = 2_200_000_000
    obs_bytes = base_resident + kv_2048
    estimator.register_observation("llama3.2:3b", resident_bytes=obs_bytes, context_length=2048)

    # Estimate for ctx 4096 (unseen context)
    est = estimator.estimate_model_memory("llama3.2:3b", context_length=4096)
    assert est.confidence == EstimateConfidence.CALIBRATED_SINGLE_POINT
    kv_4096 = 469762048
    expected_total = base_resident + kv_4096
    assert est.total_estimated_bytes == expected_total
    assert est.kv_cache_bytes == kv_4096

    # Register second observation -> upgrades to CALIBRATED_MULTI_POINT
    estimator.register_observation(
        "llama3.2:3b", resident_bytes=expected_total, context_length=4096
    )
    est_8k = estimator.estimate_model_memory("llama3.2:3b", context_length=8192)
    assert est_8k.confidence == EstimateConfidence.CALIBRATED_MULTI_POINT


def test_tier_3_theoretical_without_observations():
    """Verify Tier 3 THEORETICAL estimate uses file_size + GQA KV + scratch + overhead."""
    estimator = ModelMemoryEstimator()
    arch = {
        "size": 2_000_000_000,
        "model_info": {
            "general.architecture": "llama",
            "llama.block_count": 28,
            "llama.attention.head_count_kv": 8,
            "llama.attention.key_length": 128,
        },
    }
    est = estimator.estimate_model_memory("llama3.2:3b", context_length=2048, arch_metadata=arch)
    assert est.confidence == EstimateConfidence.THEORETICAL
    assert est.weights_bytes == 2_000_000_000
    assert est.kv_cache_bytes == 234881024
    assert est.compute_scratch_bytes == estimator.DEFAULT_SCRATCH_BYTES
    assert est.runtime_overhead_bytes == estimator.DEFAULT_RUNTIME_OVERHEAD_BYTES
    assert est.total_estimated_bytes == (
        2_000_000_000
        + 234881024
        + estimator.DEFAULT_SCRATCH_BYTES
        + estimator.DEFAULT_RUNTIME_OVERHEAD_BYTES
    )


def test_tier_4_heuristic_fallback():
    """Verify Tier 4 HEURISTIC estimate when metadata is absent or minimal."""
    estimator = ModelMemoryEstimator()
    est = estimator.estimate_model_memory("unknown-model:latest", context_length=2048)
    assert est.confidence == EstimateConfidence.HEURISTIC
    assert est.total_estimated_bytes > 0
    assert est.weights_bytes > 0
    assert est.kv_cache_bytes > 0
    assert "Incomplete or unavailable" in est.assumptions[0]


def test_calibration_drift_invalidation():
    """Verify that observation with >10% drift replaces prior calibration baseline."""
    estimator = ModelMemoryEstimator()
    estimator.register_observation("llama3.2:3b", resident_bytes=2_000_000_000, context_length=2048)
    assert len(estimator._observations["llama3.2:3b"]) == 1

    # Same context, within 10% drift -> ignored
    estimator.register_observation("llama3.2:3b", resident_bytes=2_050_000_000, context_length=2048)
    assert len(estimator._observations["llama3.2:3b"]) == 1
    assert estimator._observations["llama3.2:3b"][0][0] == 2_000_000_000

    # Same context, >10% drift (e.g. 2.4 GB vs 2.0 GB = 20% drift) -> re-baselined
    estimator.register_observation("llama3.2:3b", resident_bytes=2_400_000_000, context_length=2048)
    assert len(estimator._observations["llama3.2:3b"]) == 1
    assert estimator._observations["llama3.2:3b"][0][0] == 2_400_000_000
