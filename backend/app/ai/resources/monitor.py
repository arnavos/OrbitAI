"""macOS (Darwin) native memory monitoring using Mach VM APIs and sysctl."""

import contextlib
import ctypes
import ctypes.util
import os
import sys
import threading
from datetime import UTC, datetime
from typing import Any

from app.ai.resources.types import (
    DerivedHeadroom,
    DirectHostMemory,
    MemoryPressureLevel,
    MemoryPressurePolicy,
    ProcessMemorySnapshot,
    SystemMemorySnapshot,
)
from app.core.logging import logger

# Mach VM statistics 64-bit structure for macOS Darwin
# Aligned with /usr/include/mach/vm_statistics.h


class _VMStatistics64(ctypes.Structure):
    _fields_ = [
        ("free_count", ctypes.c_uint32),
        ("active_count", ctypes.c_uint32),
        ("inactive_count", ctypes.c_uint32),
        ("wire_count", ctypes.c_uint32),
        ("zero_fill_count", ctypes.c_uint64),
        ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64),
        ("pageouts", ctypes.c_uint64),
        ("faults", ctypes.c_uint64),
        ("cow_faults", ctypes.c_uint64),
        ("lookups", ctypes.c_uint64),
        ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64),
        ("purgeable_count", ctypes.c_uint32),
        ("speculative_count", ctypes.c_uint32),
        ("decompressions", ctypes.c_uint64),
        ("compressions", ctypes.c_uint64),
        ("swapins", ctypes.c_uint64),
        ("swapouts", ctypes.c_uint64),
        ("compressor_page_count", ctypes.c_uint32),
        ("throttled_count", ctypes.c_uint32),
        ("external_page_count", ctypes.c_uint32),
        ("internal_page_count", ctypes.c_uint32),
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
        ("swapped_count", ctypes.c_uint64),
    ]


class _XSWUsage(ctypes.Structure):
    _fields_ = [
        ("xsu_total", ctypes.c_uint64),
        ("xsu_avail", ctypes.c_uint64),
        ("xsu_used", ctypes.c_uint64),
        ("xsu_pagesize", ctypes.c_uint32),
        ("xsu_encrypted", ctypes.c_uint32),
    ]


class _RUsageInfoV4(ctypes.Structure):
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
        ("ri_child_user_time", ctypes.c_uint64),
        ("ri_child_system_time", ctypes.c_uint64),
        ("ri_child_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_child_interrupt_wkups", ctypes.c_uint64),
        ("ri_child_pageins", ctypes.c_uint64),
        ("ri_child_elapsed_abstime", ctypes.c_uint64),
        ("ri_diskio_bytesread", ctypes.c_uint64),
        ("ri_diskio_byteswritten", ctypes.c_uint64),
        ("ri_cpu_time_qos_default", ctypes.c_uint64),
        ("ri_cpu_time_qos_maintenance", ctypes.c_uint64),
        ("ri_cpu_time_qos_background", ctypes.c_uint64),
        ("ri_cpu_time_qos_utility", ctypes.c_uint64),
        ("ri_cpu_time_qos_legacy", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_initiated", ctypes.c_uint64),
        ("ri_cpu_time_qos_user_interactive", ctypes.c_uint64),
        ("ri_billed_system_time", ctypes.c_uint64),
        ("ri_serviced_system_time", ctypes.c_uint64),
        ("ri_logical_writes", ctypes.c_uint64),
        ("ri_lifetime_max_phys_footprint", ctypes.c_uint64),
        ("ri_instructions", ctypes.c_uint64),
        ("ri_cycles", ctypes.c_uint64),
        ("ri_billed_energy", ctypes.c_uint64),
        ("ri_serviced_energy", ctypes.c_uint64),
        ("ri_interval_max_phys_footprint", ctypes.c_uint64),
        ("ri_runnable_time", ctypes.c_uint64),
    ]


if sys.platform == "darwin":
    # Compile-time ABI sanity checks against Darwin macOS kernel definitions
    assert ctypes.sizeof(_VMStatistics64) == 160, (
        f"ABI mismatch: _VMStatistics64 is {ctypes.sizeof(_VMStatistics64)}"
    )
    assert ctypes.sizeof(_XSWUsage) == 32, f"ABI mismatch: _XSWUsage is {ctypes.sizeof(_XSWUsage)}"
    assert ctypes.sizeof(_RUsageInfoV4) == 296, (
        f"ABI mismatch: _RUsageInfoV4 is {ctypes.sizeof(_RUsageInfoV4)}"
    )
    assert _RUsageInfoV4.ri_phys_footprint.offset == 72
    assert _RUsageInfoV4.ri_logical_writes.offset == 232
    assert _VMStatistics64.swapped_count.offset == 152


class MacOSMemoryMonitor:
    """Zero-dependency native memory monitor for macOS using Mach APIs and sysctl."""

    HOST_VM_INFO64 = 4
    HOST_VM_INFO64_COUNT = 40
    RUSAGE_INFO_V4 = 4
    MACH_PORT_NULL = 0

    def __init__(self) -> None:
        self._is_darwin = sys.platform == "darwin"
        self._libc = None
        self._host_port: int | None = None
        self._is_closing = False
        self._is_closed = False
        self._active_ops: int = 0
        self._lock = threading.RLock()
        self._ops_done = threading.Condition(self._lock)
        self._init_error: str | None = None

        if self._is_darwin:
            try:
                c_lib_path = ctypes.util.find_library("c")
                if c_lib_path:
                    self._libc = ctypes.CDLL(c_lib_path)
                    self._init_native_bindings()
                    port = self._libc.mach_host_self()
                    if port != self.MACH_PORT_NULL:
                        self._host_port = port
                    else:
                        self._init_error = "mach_host_self returned MACH_PORT_NULL (0)"
                        logger.warning(self._init_error)
                else:
                    self._init_error = "Darwin libc library not found via find_library('c')"
                    logger.warning(self._init_error)
            except Exception as err:
                self._init_error = f"Failed to load Darwin libc for memory monitoring: {err}"
                logger.warning(self._init_error)
                self._host_port = None
        else:
            self._init_error = f"Non-Darwin platform ({sys.platform})"

    def _init_native_bindings(self) -> None:
        """Bind explicit argument and return types for Darwin native C library calls."""
        if self._libc is None:
            return

        # mach_port_t mach_host_self(void)
        self._libc.mach_host_self.argtypes = []
        self._libc.mach_host_self.restype = ctypes.c_uint32

        # mach_port_t mach_task_self(void)
        self._libc.mach_task_self.argtypes = []
        self._libc.mach_task_self.restype = ctypes.c_uint32

        # kern_return_t mach_port_deallocate(ipc_space_t task, mach_port_name_t name)
        self._libc.mach_port_deallocate.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
        ]
        self._libc.mach_port_deallocate.restype = ctypes.c_int

        # kern_return_t host_page_size(host_t host, vm_size_t *out_page_size)
        self._libc.host_page_size.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        self._libc.host_page_size.restype = ctypes.c_int

        # kern_return_t host_statistics64(host_t, host_flavor_t,
        #                                  host_info64_t, mach_msg_type_number_t *)
        self._libc.host_statistics64.argtypes = [
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        self._libc.host_statistics64.restype = ctypes.c_int

        # int sysctlbyname(const char *, void *, size_t *, void *, size_t)
        self._libc.sysctlbyname.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        self._libc.sysctlbyname.restype = ctypes.c_int

        # int proc_pid_rusage(int pid, int flavor, rusage_info_t *buffer)
        self._libc.proc_pid_rusage.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
        ]
        self._libc.proc_pid_rusage.restype = ctypes.c_int

    @contextlib.contextmanager
    def _native_operation(self):
        """Track active native operations and prevent calls during or after shutdown."""
        acquired = False
        with self._lock:
            if not self._is_closing and not self._is_closed and self._host_port is not None:
                self._active_ops += 1
                acquired = True
                port = self._host_port
            else:
                port = None

        try:
            yield port
        finally:
            if acquired:
                with self._lock:
                    self._active_ops -= 1
                    if self._active_ops == 0:
                        self._ops_done.notify_all()

    @property
    def is_darwin(self) -> bool:
        """Return True if running on Darwin macOS with active libc bindings and valid host port."""
        with self._lock:
            return (
                self._is_darwin
                and self._libc is not None
                and self._host_port is not None
                and not self._is_closing
                and not self._is_closed
            )

    @property
    def is_closed(self) -> bool:
        """Return True if monitor has been shut down or is in process of closing."""
        with self._lock:
            return self._is_closing or self._is_closed

    def close(self) -> None:
        """Release owned Mach host-port send right idempotently and thread-safely."""
        with self._lock:
            if self._is_closed:
                return
            if self._is_closing:
                while not self._is_closed:
                    self._ops_done.wait()
                return
            self._is_closing = True

            # Wait until all in-flight native operations finish
            while self._active_ops > 0:
                self._ops_done.wait()

            port = self._host_port
            self._host_port = None

        try:
            if self._libc is not None and port is not None and port != self.MACH_PORT_NULL:
                try:
                    task = self._libc.mach_task_self()
                    ret = self._libc.mach_port_deallocate(task, port)
                    if ret != 0:
                        logger.warning(f"mach_port_deallocate returned code {ret}")
                except Exception as err:
                    logger.warning(f"Failed to deallocate Mach host port: {err}")
        finally:
            with self._lock:
                self._is_closed = True
                self._ops_done.notify_all()

    def __enter__(self) -> "MacOSMemoryMonitor":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def get_direct_host_memory(self) -> DirectHostMemory:
        """Sample authoritative direct memory metrics from the Darwin kernel."""
        now = datetime.now(UTC)
        with self._native_operation() as host:
            if host is None or not self._is_darwin:
                diag = self._init_error or (
                    "Monitor shut down" if self.is_closed else "Native Mach host port unavailable"
                )
                return DirectHostMemory(
                    total_physical_bytes=None,
                    free_bytes=None,
                    wired_bytes=None,
                    active_bytes=None,
                    inactive_bytes=None,
                    compressor_bytes=None,
                    purgeable_bytes=None,
                    external_page_bytes=None,
                    swap_used_bytes=None,
                    swap_total_bytes=None,
                    pressure_level=MemoryPressureLevel.UNKNOWN,
                    kernel_pressure_raw=None,
                    measured_at=now,
                    is_available=False,
                    diagnostic_message=diag,
                )

            try:
                assert self._libc is not None

                # 1. Total physical RAM via sysctl hw.memsize
                memsize = ctypes.c_uint64()
                memsize_sz = ctypes.c_size_t(ctypes.sizeof(memsize))
                ret_mem = self._libc.sysctlbyname(
                    b"hw.memsize", ctypes.byref(memsize), ctypes.byref(memsize_sz), None, 0
                )
                if ret_mem != 0:
                    logger.warning(f"hw.memsize sysctl failed with code {ret_mem}")
                    return DirectHostMemory(
                        total_physical_bytes=None,
                        free_bytes=None,
                        wired_bytes=None,
                        active_bytes=None,
                        inactive_bytes=None,
                        compressor_bytes=None,
                        purgeable_bytes=None,
                        external_page_bytes=None,
                        swap_used_bytes=None,
                        swap_total_bytes=None,
                        pressure_level=MemoryPressureLevel.UNKNOWN,
                        kernel_pressure_raw=None,
                        measured_at=now,
                        is_available=False,
                        diagnostic_message=f"hw.memsize query failed with code {ret_mem}",
                    )
                total_physical = memsize.value

                # 2. Host page size and Mach VM statistics using owned host port
                page_size = ctypes.c_size_t()
                self._libc.host_page_size(host, ctypes.byref(page_size))
                p_size = page_size.value or 16384

                vm_stats = _VMStatistics64()
                count = ctypes.c_uint32(ctypes.sizeof(_VMStatistics64) // 4)
                ret_vm = self._libc.host_statistics64(
                    host, self.HOST_VM_INFO64, ctypes.byref(vm_stats), ctypes.byref(count)
                )
                if ret_vm != 0:
                    logger.warning(f"host_statistics64 failed with return code {ret_vm}")
                    return DirectHostMemory(
                        total_physical_bytes=total_physical,
                        free_bytes=None,
                        wired_bytes=None,
                        active_bytes=None,
                        inactive_bytes=None,
                        compressor_bytes=None,
                        purgeable_bytes=None,
                        external_page_bytes=None,
                        swap_used_bytes=None,
                        swap_total_bytes=None,
                        pressure_level=MemoryPressureLevel.UNKNOWN,
                        kernel_pressure_raw=None,
                        measured_at=now,
                        is_available=False,
                        diagnostic_message=(
                            f"host_statistics64 probe failed with return code {ret_vm}"
                        ),
                    )

                # 3. Kernel memory pressure level via sysctl kern.memorystatus_vm_pressure_level
                pres = ctypes.c_int()
                pres_size = ctypes.c_size_t(ctypes.sizeof(pres))
                ret_pres = self._libc.sysctlbyname(
                    b"kern.memorystatus_vm_pressure_level",
                    ctypes.byref(pres),
                    ctypes.byref(pres_size),
                    None,
                    0,
                )
                raw_pressure = pres.value if ret_pres == 0 else None
                pressure_level = (
                    MemoryPressureLevel.NORMAL
                    if raw_pressure == 1
                    else MemoryPressureLevel.WARNING
                    if raw_pressure == 2
                    else MemoryPressureLevel.CRITICAL
                    if raw_pressure == 4
                    else MemoryPressureLevel.UNKNOWN
                )

                # 4. Swap usage via sysctl vm.swapusage
                swap = _XSWUsage()
                swap_size = ctypes.c_size_t(ctypes.sizeof(swap))
                ret_swap = self._libc.sysctlbyname(
                    b"vm.swapusage", ctypes.byref(swap), ctypes.byref(swap_size), None, 0
                )
                swap_used = swap.xsu_used if ret_swap == 0 else 0
                swap_total = swap.xsu_total if ret_swap == 0 else 0

                diag_msg = None
                if ret_pres != 0:
                    diag_msg = f"kern.memorystatus_vm_pressure_level failed with code {ret_pres}"

                return DirectHostMemory(
                    total_physical_bytes=total_physical,
                    free_bytes=vm_stats.free_count * p_size,
                    wired_bytes=vm_stats.wire_count * p_size,
                    active_bytes=vm_stats.active_count * p_size,
                    inactive_bytes=vm_stats.inactive_count * p_size,
                    compressor_bytes=vm_stats.compressor_page_count * p_size,
                    purgeable_bytes=vm_stats.purgeable_count * p_size,
                    external_page_bytes=vm_stats.external_page_count * p_size,
                    swap_used_bytes=swap_used,
                    swap_total_bytes=swap_total,
                    pressure_level=pressure_level,
                    kernel_pressure_raw=raw_pressure,
                    measured_at=now,
                    is_available=True,
                    diagnostic_message=diag_msg,
                )
            except Exception as exc:
                logger.error(f"Error sampling Darwin kernel memory telemetry: {exc}")
                return DirectHostMemory(
                    total_physical_bytes=None,
                    free_bytes=None,
                    wired_bytes=None,
                    active_bytes=None,
                    inactive_bytes=None,
                    compressor_bytes=None,
                    purgeable_bytes=None,
                    external_page_bytes=None,
                    swap_used_bytes=None,
                    swap_total_bytes=None,
                    pressure_level=MemoryPressureLevel.UNKNOWN,
                    kernel_pressure_raw=None,
                    measured_at=now,
                    is_available=False,
                    diagnostic_message=f"Telemetry sampling exception: {exc}",
                )

    def compute_derived_headroom(
        self,
        direct: DirectHostMemory,
        policy: MemoryPressurePolicy,
    ) -> DerivedHeadroom:
        """Derive safe available memory headroom from direct telemetry and policy settings."""
        now = datetime.now(UTC)

        is_conservative = (
            not direct.is_available
            or direct.pressure_level == MemoryPressureLevel.UNKNOWN
            or direct.free_bytes is None
        )

        if is_conservative:
            return DerivedHeadroom(
                reclaimable_bytes=0,
                effective_host_headroom_bytes=0,
                os_safety_reserve_bytes=policy.os_safety_reserve_bytes,
                inactive_reclaim_factor=0.0,
                is_pressure_gated=False,
                computed_at=now,
                is_conservative=True,
            )

        free_bytes = direct.free_bytes or 0
        purgeable_bytes = direct.purgeable_bytes or 0
        inactive_bytes = direct.inactive_bytes or 0

        # In Darwin, purgeable pages reside within active/inactive queues.
        # To avoid double-counting, non-purgeable inactive is calculated:
        non_purgeable_inactive = max(0, inactive_bytes - purgeable_bytes)
        eff_reclaim_factor = policy.inactive_reclaim_factor

        reclaimable = (
            free_bytes + purgeable_bytes + int(eff_reclaim_factor * non_purgeable_inactive)
        )

        # Apply kernel pressure gating
        is_pressure_gated = False
        if direct.pressure_level == MemoryPressureLevel.CRITICAL:
            effective_headroom = 0
            is_pressure_gated = True
        elif direct.pressure_level == MemoryPressureLevel.WARNING:
            # Under warning, discount net headroom by 50%
            net = max(0, reclaimable - policy.os_safety_reserve_bytes)
            effective_headroom = int(net * 0.50)
        else:
            effective_headroom = max(0, reclaimable - policy.os_safety_reserve_bytes)

        return DerivedHeadroom(
            reclaimable_bytes=reclaimable,
            effective_host_headroom_bytes=effective_headroom,
            os_safety_reserve_bytes=policy.os_safety_reserve_bytes,
            inactive_reclaim_factor=eff_reclaim_factor,
            is_pressure_gated=is_pressure_gated,
            computed_at=now,
            is_conservative=False,
        )

    def get_system_snapshot(
        self,
        policy: MemoryPressurePolicy | None = None,
    ) -> SystemMemorySnapshot:
        """Sample direct host telemetry and compute derived headroom atomically."""
        eff_policy = policy or MemoryPressurePolicy()
        direct = self.get_direct_host_memory()
        derived = self.compute_derived_headroom(direct, eff_policy)
        return SystemMemorySnapshot(
            direct=direct,
            derived=derived,
            timestamp=direct.measured_at,
        )

    def get_process_memory(self, pid: int | None = None) -> ProcessMemorySnapshot:
        """Sample process physical footprint and resident size via proc_pid_rusage."""
        now = datetime.now(UTC)
        target_pid = pid if pid is not None else os.getpid()

        with self._native_operation():
            if not self.is_darwin:
                return ProcessMemorySnapshot(
                    pid=target_pid,
                    physical_footprint_bytes=52_428_800,  # 50 MB fallback
                    resident_size_bytes=52_428_800,
                    measured_at=now,
                    is_available=False,
                    diagnostic_message=self._init_error or "Native process monitoring unavailable",
                )

            try:
                assert self._libc is not None
                info = _RUsageInfoV4()
                ret = self._libc.proc_pid_rusage(
                    target_pid, self.RUSAGE_INFO_V4, ctypes.byref(info)
                )
                if ret != 0:
                    diag = f"proc_pid_rusage failed for pid {target_pid} with code {ret}"
                    logger.warning(diag)
                    return ProcessMemorySnapshot(
                        pid=target_pid,
                        physical_footprint_bytes=0,
                        resident_size_bytes=0,
                        measured_at=now,
                        is_available=False,
                        diagnostic_message=diag,
                    )

                return ProcessMemorySnapshot(
                    pid=target_pid,
                    physical_footprint_bytes=info.ri_phys_footprint,
                    resident_size_bytes=info.ri_resident_size,
                    measured_at=now,
                    is_available=True,
                    diagnostic_message=None,
                )
            except Exception as exc:
                diag = f"Error sampling process memory for pid {target_pid}: {exc}"
                logger.error(diag)
                return ProcessMemorySnapshot(
                    pid=target_pid,
                    physical_footprint_bytes=0,
                    resident_size_bytes=0,
                    measured_at=now,
                    is_available=False,
                    diagnostic_message=diag,
                )
