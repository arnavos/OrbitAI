"""Deterministic test suite for Darwin native Mach/sysctl resource monitoring."""

import ctypes
import sys
import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.ai.resources.manager import ResourceManager
from app.ai.resources.monitor import (
    MacOSMemoryMonitor,
    _RUsageInfoV4,
    _VMStatistics64,
    _XSWUsage,
)
from app.ai.resources.types import (
    AdmissionOutcome,
    DerivedHeadroom,
    DirectHostMemory,
    MemoryPressureLevel,
    MemoryPressurePolicy,
    ProcessMemorySnapshot,
    SystemMemorySnapshot,
)


def test_derived_headroom_calculation_with_reserve():
    """Verify effective headroom formula respects inactive reclaim, purgeable deduplication, and OS reserve."""
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),  # 16 GB
        free_bytes=2 * (1024**3),  # 2 GB
        active_bytes=6 * (1024**3),  # 6 GB
        inactive_bytes=4 * (1024**3),  # 4 GB
        wired_bytes=3 * (1024**3),  # 3 GB
        compressor_bytes=500 * (1024**2),
        purgeable_bytes=1 * (1024**3),  # 1 GB
        external_page_bytes=2 * (1024**3),
        swap_used_bytes=0,
        swap_total_bytes=0,
        pressure_level=MemoryPressureLevel.NORMAL,
    )
    policy = MemoryPressurePolicy(
        os_safety_reserve_bytes=3 * (1024**3),  # 3 GB reserve
        inactive_reclaim_factor=0.30,
    )
    monitor = MacOSMemoryMonitor()
    headroom = monitor.compute_derived_headroom(direct, policy)

    # In Darwin deduplication: non_purgeable_inactive = 4 GB - 1 GB = 3 GB
    # Reclaimable = 2 GB (free) + 1 GB (purgeable) + 0.3 * 3 GB = 3.9 GB
    # Effective headroom = 3.9 GB - 3.0 GB reserve = 0.9 GB
    non_purgeable_inactive = direct.inactive_bytes - direct.purgeable_bytes
    expected_reclaimable = (
        direct.free_bytes + direct.purgeable_bytes + int(0.30 * non_purgeable_inactive)
    )
    expected_headroom = expected_reclaimable - policy.os_safety_reserve_bytes

    assert headroom.reclaimable_bytes == expected_reclaimable
    assert headroom.effective_host_headroom_bytes == expected_headroom
    assert headroom.os_safety_reserve_bytes == policy.os_safety_reserve_bytes
    assert not headroom.is_pressure_gated


def test_headroom_clamped_to_zero_when_reserve_exceeds_reclaimable():
    """Verify effective headroom never reports negative values."""
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),
        free_bytes=500 * (1024**2),
        active_bytes=10 * (1024**3),
        inactive_bytes=1 * (1024**3),
        wired_bytes=4 * (1024**3),
        compressor_bytes=0,
        purgeable_bytes=200 * (1024**2),
        external_page_bytes=0,
        swap_used_bytes=0,
        swap_total_bytes=0,
        pressure_level=MemoryPressureLevel.NORMAL,
    )
    policy = MemoryPressurePolicy(
        os_safety_reserve_bytes=3 * (1024**3),  # 3 GB reserve
        inactive_reclaim_factor=0.30,
    )
    monitor = MacOSMemoryMonitor()
    headroom = monitor.compute_derived_headroom(direct, policy)

    assert headroom.reclaimable_bytes > 0
    assert headroom.effective_host_headroom_bytes == 0


def test_pressure_level_gating_behavior():
    """Verify CRITICAL and WARNING pressure levels correctly gate or discount headroom."""
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),
        free_bytes=4 * (1024**3),
        active_bytes=6 * (1024**3),
        inactive_bytes=2 * (1024**3),
        wired_bytes=3 * (1024**3),
        compressor_bytes=0,
        purgeable_bytes=500 * (1024**2),
        external_page_bytes=0,
        swap_used_bytes=0,
        swap_total_bytes=0,
        pressure_level=MemoryPressureLevel.CRITICAL,
    )
    policy = MemoryPressurePolicy(
        os_safety_reserve_bytes=2 * (1024**3),
        inactive_reclaim_factor=0.30,
    )
    monitor = MacOSMemoryMonitor()

    # Under CRITICAL pressure, effective headroom is zeroed
    headroom_crit = monitor.compute_derived_headroom(direct, policy)
    assert headroom_crit.effective_host_headroom_bytes == 0
    assert headroom_crit.is_pressure_gated

    # Under WARNING pressure, net headroom is discounted by 50%
    direct.pressure_level = MemoryPressureLevel.WARNING
    headroom_warn = monitor.compute_derived_headroom(direct, policy)
    assert headroom_warn.effective_host_headroom_bytes > 0
    # Compare with NORMAL
    direct.pressure_level = MemoryPressureLevel.NORMAL
    headroom_norm = monitor.compute_derived_headroom(direct, policy)
    assert headroom_warn.effective_host_headroom_bytes == int(
        headroom_norm.effective_host_headroom_bytes * 0.50
    )


def test_non_darwin_platform_fallback():
    """Verify monitor safely provides fallback data when executed on non-Darwin OS."""
    with patch("sys.platform", "linux"):
        monitor = MacOSMemoryMonitor()
        assert not monitor.is_darwin
        snapshot = monitor.get_system_snapshot()
        assert isinstance(snapshot, SystemMemorySnapshot)
        assert snapshot.direct.is_available is False
        assert snapshot.direct.free_bytes is None
        assert snapshot.direct.total_physical_bytes is None
        assert snapshot.direct.pressure_level == MemoryPressureLevel.UNKNOWN
        assert snapshot.derived.effective_host_headroom_bytes == 0
        assert snapshot.derived.is_conservative is True

        proc = monitor.get_process_memory()
        assert isinstance(proc, ProcessMemorySnapshot)
        assert proc.physical_footprint_bytes > 0


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin-specific native Mach VM test")
def test_live_darwin_mach_telemetry_probe():
    """Verify native Mach VM / sysctl telemetry queries on live macOS host."""
    monitor = MacOSMemoryMonitor()
    try:
        assert monitor.is_darwin

        start_time = datetime.now(UTC)
        snapshot = monitor.get_system_snapshot()
        end_time = datetime.now(UTC)

        # Verification of physical host reality
        assert snapshot.direct.total_physical_bytes >= 8 * (1024**3)  # At least 8 GB
        assert snapshot.direct.active_bytes > 0
        assert snapshot.direct.wired_bytes > 0
        assert snapshot.direct.pressure_level in (
            MemoryPressureLevel.NORMAL,
            MemoryPressureLevel.WARNING,
            MemoryPressureLevel.CRITICAL,
        )

        # Headroom separation
        assert isinstance(snapshot.derived, DerivedHeadroom)
        assert snapshot.derived.effective_host_headroom_bytes >= 0

        # Freshness
        assert start_time <= snapshot.timestamp <= end_time
    finally:
        monitor.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin-specific proc_pid_rusage test")
def test_live_darwin_process_rusage_footprint():
    """Verify proc_pid_rusage returns real resident physical footprint for current process."""
    monitor = MacOSMemoryMonitor()
    try:
        proc = monitor.get_process_memory()

        assert isinstance(proc, ProcessMemorySnapshot)
        assert proc.pid > 0
        # Process physical footprint on Apple Silicon should be positive (> 1 MB)
        assert proc.physical_footprint_bytes > 1024 * 1024
        assert proc.resident_size_bytes > 0
    finally:
        monitor.close()


def test_mach_port_single_acquisition_and_reuse_mocked():
    """Verify monitor acquires host port exactly once during init and reuses it for all samples."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 9999
        mock_libc.host_page_size.return_value = 0
        mock_libc.host_statistics64.return_value = 0
        mock_libc.sysctlbyname.return_value = 0

        monitor = MacOSMemoryMonitor()
        assert monitor.is_darwin
        assert mock_libc.mach_host_self.call_count == 1

        # Multiple telemetry samples must reuse the owned host port without re-calling mach_host_self
        for _ in range(5):
            monitor.get_direct_host_memory()

        assert mock_libc.mach_host_self.call_count == 1
        # Check that host_page_size and host_statistics64 received host port 9999
        assert mock_libc.host_page_size.call_args[0][0] == 9999
        assert mock_libc.host_statistics64.call_args[0][0] == 9999

        monitor.close()


def test_mach_port_deallocation_and_idempotent_shutdown_mocked():
    """Verify monitor releases owned port exactly once and shutdown is strictly idempotent."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 4321
        mock_libc.mach_task_self.return_value = 100
        mock_libc.mach_port_deallocate.return_value = 0

        monitor = MacOSMemoryMonitor()
        assert monitor.is_darwin
        assert not monitor.is_closed

        # First close: deallocates exactly once
        monitor.close()
        assert monitor.is_closed
        assert not monitor.is_darwin
        mock_libc.mach_port_deallocate.assert_called_once_with(100, 4321)

        # Second close: no-op, deallocate is NOT called again
        monitor.close()
        assert mock_libc.mach_port_deallocate.call_count == 1


def test_mach_port_no_use_after_shutdown_mocked():
    """Verify that calling telemetry on a closed monitor uses safe fallback without native calls."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 5555
        mock_libc.mach_task_self.return_value = 100
        mock_libc.mach_port_deallocate.return_value = 0

        monitor = MacOSMemoryMonitor()
        monitor.close()

        # Call telemetry after shutdown
        direct = monitor.get_direct_host_memory()
        assert direct.is_available is False
        assert direct.free_bytes is None
        assert direct.total_physical_bytes is None
        assert direct.pressure_level == MemoryPressureLevel.UNKNOWN
        # No native Mach calls should have been made after shutdown
        assert mock_libc.host_page_size.called is False
        assert mock_libc.host_statistics64.called is False

        proc = monitor.get_process_memory()
        assert proc.physical_footprint_bytes > 0
        assert mock_libc.proc_pid_rusage.called is False


def test_mach_port_cleanup_after_telemetry_failure_mocked():
    """Verify monitor safely deallocates host port even if telemetry queries throw exceptions."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 8888
        mock_libc.mach_task_self.return_value = 100
        mock_libc.mach_port_deallocate.return_value = 0
        # Simulate failure in host_statistics64
        mock_libc.host_statistics64.side_effect = RuntimeError("Kernel Mach IPC timeout")

        monitor = MacOSMemoryMonitor()
        # Telemetry handles exception and returns fallback
        direct = monitor.get_direct_host_memory()
        assert direct.pressure_level == MemoryPressureLevel.UNKNOWN

        # Cleanup still executes cleanly
        monitor.close()
        mock_libc.mach_port_deallocate.assert_called_once_with(100, 8888)


def test_mach_port_safe_behavior_if_init_fails_mocked():
    """Verify monitor handles MACH_PORT_NULL or initialization exception safely without crashing."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        # Scenario 1: mach_host_self returns MACH_PORT_NULL (0)
        mock_libc.mach_host_self.return_value = 0

        monitor = MacOSMemoryMonitor()
        assert not monitor.is_darwin
        # Calling close() must not attempt deallocation on 0/None
        monitor.close()
        assert mock_libc.mach_port_deallocate.called is False

        # Scenario 2: mach_host_self raises exception
        mock_libc.mach_host_self.side_effect = OSError("Permission denied")
        monitor2 = MacOSMemoryMonitor()
        assert not monitor2.is_darwin
        monitor2.close()
        assert mock_libc.mach_port_deallocate.called is False


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin-specific Mach port reference test")
def test_live_darwin_mach_port_reference_lifecycle():
    """Verify real Darwin Mach host port user-reference count before, during, and after monitor."""
    import ctypes

    libc = ctypes.CDLL("/usr/lib/libc.dylib")
    libc.mach_host_self.restype = ctypes.c_uint32
    libc.mach_task_self.restype = ctypes.c_uint32
    libc.mach_port_deallocate.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    libc.mach_port_deallocate.restype = ctypes.c_int
    libc.mach_port_get_refs.argtypes = [
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    libc.mach_port_get_refs.restype = ctypes.c_int

    task = libc.mach_task_self()
    base_host = libc.mach_host_self()
    try:
        refs_before = ctypes.c_uint32()
        assert libc.mach_port_get_refs(task, base_host, 0, ctypes.byref(refs_before)) == 0

        # 1. Initialize monitor: should increment ref count by exactly 1
        monitor = MacOSMemoryMonitor()
        assert monitor.is_darwin

        refs_active = ctypes.c_uint32()
        assert libc.mach_port_get_refs(task, base_host, 0, ctypes.byref(refs_active)) == 0
        assert refs_active.value == refs_before.value + 1

        # 2. Repeated sampling: 10 queries must NOT leak references
        for _ in range(10):
            monitor.get_direct_host_memory()

        refs_sampled = ctypes.c_uint32()
        assert libc.mach_port_get_refs(task, base_host, 0, ctypes.byref(refs_sampled)) == 0
        assert refs_sampled.value == refs_active.value

        # 3. Shutdown monitor: ref count must return to baseline
        monitor.close()
        refs_after = ctypes.c_uint32()
        assert libc.mach_port_get_refs(task, base_host, 0, ctypes.byref(refs_after)) == 0
        assert refs_after.value == refs_before.value

        # 4. Idempotent second close: no change in ref count
        monitor.close()
        refs_after2 = ctypes.c_uint32()
        assert libc.mach_port_get_refs(task, base_host, 0, ctypes.byref(refs_after2)) == 0
        assert refs_after2.value == refs_before.value
    finally:
        libc.mach_port_deallocate(task, base_host)


def test_telemetry_operation_overlapping_shutdown_and_waiting():
    """Verify shutdown blocks for in-flight native calls, deallocates after, and rejects new calls."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 8888
        mock_libc.mach_task_self.return_value = 100
        mock_libc.mach_port_deallocate.return_value = 0
        mock_libc.host_page_size.return_value = 0
        mock_libc.sysctlbyname.return_value = 0

        op_started = threading.Event()
        op_can_finish = threading.Event()

        def slow_host_stats(*args):
            op_started.set()
            op_can_finish.wait(timeout=2.0)
            return 0

        mock_libc.host_statistics64.side_effect = slow_host_stats

        monitor = MacOSMemoryMonitor()
        telemetry_result: list[DirectHostMemory] = []

        def run_telemetry():
            res = monitor.get_direct_host_memory()
            telemetry_result.append(res)

        op_thread = threading.Thread(target=run_telemetry)
        op_thread.start()

        # Wait until telemetry operation is executing native call
        assert op_started.wait(timeout=2.0)

        # In parallel, start close() on another thread
        close_thread = threading.Thread(target=monitor.close)
        close_thread.start()
        close_thread.join(timeout=0.1)

        # close() must still be running (blocked waiting for in-flight native operation)
        assert close_thread.is_alive()
        # Deallocation must NOT have happened while operation was running
        assert mock_libc.mach_port_deallocate.called is False

        # Attempt a new telemetry call while close() is waiting: must return fallback without native calls
        call_count_before = mock_libc.host_statistics64.call_count
        subsequent_direct = monitor.get_direct_host_memory()
        assert subsequent_direct.is_available is False
        assert mock_libc.host_statistics64.call_count == call_count_before

        # Allow in-flight operation to finish
        op_can_finish.set()
        op_thread.join(timeout=2.0)
        close_thread.join(timeout=2.0)

        # Now close() has finished and deallocation occurred exactly once
        assert mock_libc.mach_port_deallocate.call_count == 1
        assert len(telemetry_result) == 1
        assert telemetry_result[0].is_available is True


def test_concurrent_shutdown_calls():
    """Verify multiple concurrent threads calling close() results in exactly one port deallocation."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 7777
        mock_libc.mach_task_self.return_value = 100
        mock_libc.mach_port_deallocate.return_value = 0
        mock_libc.host_page_size.return_value = 0
        mock_libc.sysctlbyname.return_value = 0

        monitor = MacOSMemoryMonitor()
        threads = [threading.Thread(target=monitor.close) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert mock_libc.mach_port_deallocate.call_count == 1
        assert monitor.is_closed is True


def test_concurrent_close_callers_wait_for_in_progress_shutdown():
    """Verify concurrent close() callers wait until shutdown has actually finished."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 7777
        mock_libc.mach_task_self.return_value = 100
        mock_libc.mach_port_deallocate.return_value = 0
        mock_libc.host_page_size.return_value = 0
        mock_libc.sysctlbyname.return_value = 0

        op_started = threading.Event()
        op_can_finish = threading.Event()

        def slow_stats(*args):
            op_started.set()
            op_can_finish.wait(timeout=2.0)
            return 0

        mock_libc.host_statistics64.side_effect = slow_stats

        monitor = MacOSMemoryMonitor()

        # Start an active native operation
        op_thread = threading.Thread(target=monitor.get_direct_host_memory)
        op_thread.start()
        assert op_started.wait(timeout=2.0)

        # Thread 1 starts close() -> sets _is_closing = True and blocks on _active_ops == 0
        close1_thread = threading.Thread(target=monitor.close)
        close1_thread.start()
        close1_thread.join(timeout=0.1)
        assert close1_thread.is_alive()

        # Thread 2 and Thread 3 call close() concurrently while Thread 1 is waiting
        close2_thread = threading.Thread(target=monitor.close)
        close3_thread = threading.Thread(target=monitor.close)
        close2_thread.start()
        close3_thread.start()

        close2_thread.join(timeout=0.1)
        close3_thread.join(timeout=0.1)

        # Concurrent close callers MUST NOT return before shutdown finishes!
        assert close2_thread.is_alive()
        assert close3_thread.is_alive()
        assert mock_libc.mach_port_deallocate.called is False

        # Now complete the active native operation
        op_can_finish.set()
        op_thread.join(timeout=2.0)

        # All close callers should now finish
        close1_thread.join(timeout=2.0)
        close2_thread.join(timeout=2.0)
        close3_thread.join(timeout=2.0)

        assert not close1_thread.is_alive()
        assert not close2_thread.is_alive()
        assert not close3_thread.is_alive()

        # Mach port deallocation must happen exactly once
        assert mock_libc.mach_port_deallocate.call_count == 1
        assert monitor.is_closed is True


def test_close_releases_waiting_threads_on_deallocation_error():
    """Verify all waiting close() callers are released even if Mach deallocation errors."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 8888
        mock_libc.mach_task_self.return_value = 100
        mock_libc.mach_port_deallocate.side_effect = RuntimeError("Kernel IPC error")

        monitor = MacOSMemoryMonitor()
        threads = [threading.Thread(target=monitor.close) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)
            assert not t.is_alive()

        assert monitor.is_closed is True


def test_unknown_pressure_produces_zero_reclaimable_and_zero_headroom():
    """Verify UNKNOWN pressure produces strictly zero reclaimable and zero headroom."""
    direct = DirectHostMemory(
        total_physical_bytes=16 * (1024**3),
        free_bytes=2 * (1024**3),
        active_bytes=4 * (1024**3),
        inactive_bytes=4 * (1024**3),
        wired_bytes=2 * (1024**3),
        compressor_bytes=0,
        purgeable_bytes=1 * (1024**3),
        external_page_bytes=0,
        swap_used_bytes=0,
        swap_total_bytes=0,
        pressure_level=MemoryPressureLevel.UNKNOWN,
        is_available=False,
    )
    policy = MemoryPressurePolicy(os_safety_reserve_bytes=1024**3)
    monitor = MacOSMemoryMonitor()
    headroom = monitor.compute_derived_headroom(direct, policy)

    assert headroom.reclaimable_bytes == 0
    assert headroom.effective_host_headroom_bytes == 0
    assert headroom.inactive_reclaim_factor == 0.0
    assert headroom.is_conservative is True
    monitor.close()


def test_native_init_failure_produces_unknown_telemetry():
    """Verify native initialization failure marks telemetry UNKNOWN/unavailable with diagnostics."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 0  # MACH_PORT_NULL

        monitor = MacOSMemoryMonitor()
        assert not monitor.is_darwin

        direct = monitor.get_direct_host_memory()
        assert direct.is_available is False
        assert direct.free_bytes is None
        assert direct.total_physical_bytes is None
        assert direct.pressure_level == MemoryPressureLevel.UNKNOWN
        assert direct.diagnostic_message is not None
        assert "MACH_PORT_NULL" in direct.diagnostic_message

        # Verify derived headroom adopts conservative zero-reclaim stance
        policy = MemoryPressurePolicy(os_safety_reserve_bytes=1024**3)
        headroom = monitor.compute_derived_headroom(direct, policy)
        assert headroom.is_conservative is True
        assert headroom.inactive_reclaim_factor == 0.0
        assert headroom.reclaimable_bytes == 0
        assert headroom.effective_host_headroom_bytes == 0

        monitor.close()


@pytest.mark.asyncio
async def test_required_telemetry_failure_resulting_in_conservative_admission():
    """Verify that failure of a required probe (host_statistics64) triggers conservative rejection."""
    with (
        patch("ctypes.util.find_library", return_value="/usr/lib/libc.dylib"),
        patch("ctypes.CDLL") as mock_cdll_cls,
        patch("sys.platform", "darwin"),
    ):
        mock_libc = MagicMock()
        mock_cdll_cls.return_value = mock_libc
        mock_libc.mach_host_self.return_value = 5555
        mock_libc.mach_task_self.return_value = 100
        mock_libc.mach_port_deallocate.return_value = 0
        mock_libc.host_page_size.return_value = 0
        # Required telemetry probe fails with return code 4
        mock_libc.host_statistics64.return_value = 4

        monitor = MacOSMemoryMonitor()
        policy = MemoryPressurePolicy(
            os_safety_reserve_bytes=1 * (1024**3),
            default_context_length=4096,
            reduced_context_length=2048,
        )
        manager = ResourceManager(monitor=monitor, policy=policy)

        decision = await manager.acquire_admission(
            model_id="llama3.2:3b",
            request_id="test-req-degraded",
            requested_context=4096,
        )

        # Under degraded/unknown telemetry, loading a new model is rejected conservatively
        assert decision.outcome == AdmissionOutcome.REJECT
        assert "telemetry unavailable" in decision.reason.lower()

        await manager.stop()


def test_darwin_native_abi_structure_sizes_alignments_and_offsets():
    """Verify ctypes ABI structures match macOS ARM64 SDK declarations exactly."""
    # 1. _VMStatistics64: 160 bytes, alignment 8
    assert ctypes.sizeof(_VMStatistics64) == 160
    assert ctypes.alignment(_VMStatistics64) == 8
    assert _VMStatistics64.free_count.offset == 0
    assert _VMStatistics64.active_count.offset == 4
    assert _VMStatistics64.inactive_count.offset == 8
    assert _VMStatistics64.wire_count.offset == 12
    assert _VMStatistics64.purgeable_count.offset == 88
    assert _VMStatistics64.compressor_page_count.offset == 128
    assert _VMStatistics64.external_page_count.offset == 136
    assert _VMStatistics64.swapped_count.offset == 152

    # 2. _XSWUsage: 32 bytes, alignment 8
    assert ctypes.sizeof(_XSWUsage) == 32
    assert ctypes.alignment(_XSWUsage) == 8
    assert _XSWUsage.xsu_total.offset == 0
    assert _XSWUsage.xsu_avail.offset == 8
    assert _XSWUsage.xsu_used.offset == 16
    assert _XSWUsage.xsu_pagesize.offset == 24
    assert _XSWUsage.xsu_encrypted.offset == 28

    # 3. _RUsageInfoV4: 296 bytes, alignment 8, 36 fields
    assert ctypes.sizeof(_RUsageInfoV4) == 296
    assert ctypes.alignment(_RUsageInfoV4) == 8
    assert _RUsageInfoV4.ri_uuid.offset == 0
    assert _RUsageInfoV4.ri_user_time.offset == 16
    assert _RUsageInfoV4.ri_system_time.offset == 24
    assert _RUsageInfoV4.ri_resident_size.offset == 64
    assert _RUsageInfoV4.ri_phys_footprint.offset == 72
    assert _RUsageInfoV4.ri_logical_writes.offset == 232
    assert _RUsageInfoV4.ri_runnable_time.offset == 288


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin-specific native stress test")
def test_live_darwin_repeated_sampling_and_shutdown_lifecycle():
    """Verify repeated native sampling and clean monitor shutdown under live Darwin environment."""
    # Test repeated sampling across 50 iterations on a single monitor
    monitor = MacOSMemoryMonitor()
    try:
        assert monitor.is_darwin
        for _ in range(50):
            sys_snap = monitor.get_system_snapshot()
            assert sys_snap.direct.is_available is True
            assert sys_snap.direct.total_physical_bytes is not None
            assert sys_snap.direct.total_physical_bytes > 0

            proc_snap = monitor.get_process_memory()
            assert proc_snap.is_available is True
            assert proc_snap.physical_footprint_bytes > 0
    finally:
        monitor.close()
        assert monitor.is_closed is True

    # Test repeated start/close lifecycle across 10 distinct monitor instances
    for _ in range(10):
        m = MacOSMemoryMonitor()
        snap = m.get_system_snapshot()
        assert snap.direct.is_available is True
        m.close()
        assert m.is_closed is True
