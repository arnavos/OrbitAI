"""Release acceptance regression and end-to-end integration test suite for ORBIT v0.1.0."""

import pytest
from pydantic import ValidationError

from app.ai.prompts import ORBIT_SYSTEM_PROMPT, resolve_system_prompt
from app.ai.resources.monitor import MacOSMemoryMonitor
from app.ai.resources.types import DirectHostMemory, MemoryPressureLevel, MemoryPressurePolicy
from app.core.config import Settings


def test_production_environment_rejects_default_dev_session_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security verification: Ensure APP_ENV=production strictly forbids dev-session-token."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ORBIT_SESSION_TOKEN", "dev-session-token")
    monkeypatch.delenv("ORBIT_SESSION_TOKEN_FILE", raising=False)

    with pytest.raises(
        ValidationError, match="Security violation: Default development session token"
    ):
        Settings()


def test_default_resource_safeguards_headroom_derivation() -> None:
    """Verify resource safeguard policy defaults provide sound headroom calculation on 16GB macOS."""
    settings = Settings(app_env="testing")
    assert settings.resource_os_safety_reserve_bytes == 1_073_741_824  # 1.0 GB
    assert settings.resource_inactive_reclaim_factor == 0.70

    mon = MacOSMemoryMonitor()
    policy = MemoryPressurePolicy(
        os_safety_reserve_bytes=settings.resource_os_safety_reserve_bytes,
        inactive_reclaim_factor=settings.resource_inactive_reclaim_factor,
    )

    # Simulated normal Darwin 16GB host: 90MB free, 3.2GB inactive, 30MB purgeable
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),
        free_bytes=90 * (1024**2),
        wired_bytes=4 * (1024**3),
        active_bytes=3 * (1024**3),
        inactive_bytes=3200 * (1024**2),
        compressor_bytes=500 * (1024**2),
        purgeable_bytes=30 * (1024**2),
        external_page_bytes=100 * (1024**2),
        swap_used_bytes=0,
        swap_total_bytes=2 * (1024**3),
        pressure_level=MemoryPressureLevel.NORMAL,
        kernel_pressure_raw=1,
        is_available=True,
    )

    headroom = mon.compute_derived_headroom(direct, policy)
    assert not headroom.is_pressure_gated
    assert not headroom.is_conservative
    # Reclaimable: 90MB + 30MB + 0.70 * (3200MB - 30MB) = 120MB + 2219MB = 2339MB (~2.28 GB)
    # Effective headroom = max(0, 2339MB - 1024MB) = 1315MB (~1.28 GB)
    assert headroom.effective_host_headroom_bytes > 1024**3
    assert headroom.reclaimable_bytes > headroom.effective_host_headroom_bytes


def test_canonical_assistant_identity_and_rejection_of_orbital() -> None:
    """Verify canonical ORBIT system prompt resolution explicitly forbids Orbital."""
    prompt = resolve_system_prompt()
    assert "You are ORBIT" in prompt
    assert "Never refer to yourself as Orbital" in prompt
    assert prompt == ORBIT_SYSTEM_PROMPT

    custom = resolve_system_prompt("Answer strictly in Spanish.")
    assert "You are ORBIT" in custom
    assert "Answer strictly in Spanish." in custom
    assert "Never refer to yourself as Orbital" in custom
