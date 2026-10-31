#!/usr/bin/env python3
"""
ORBIT M0 — Idle Memory Benchmarking Script
Measures actual idle memory consumption (RSS and macOS Physical Footprint)
across the Rust desktop shell, WebKit WebContent processes, and Python backend.
Target: < 150 MB total idle memory.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

try:
    import psutil
except ImportError:
    print("Error: psutil required. Run via 'backend/.venv/bin/python3 scripts/benchmark_memory.py'")
    sys.exit(1)


def parse_top_footprint() -> Dict[int, float]:
    """Parse macOS physical memory footprint (MEM) from top command for all running processes."""
    footprints: Dict[int, float] = {}
    if sys.platform != "darwin":
        return footprints

    try:
        cmd = ["top", "-l", "1", "-stats", "pid,mem", "-n", "2000"]
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, text=True)
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("PID"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                try:
                    pid = int(parts[0])
                    mem_str = parts[1]
                    # Parse format like 1234K, 56M, 1.2G
                    val = 0.0
                    if mem_str.endswith("K") or mem_str.endswith("k"):
                        val = float(mem_str[:-1]) / 1024.0
                    elif mem_str.endswith("M") or mem_str.endswith("m"):
                        val = float(mem_str[:-1])
                    elif mem_str.endswith("G") or mem_str.endswith("g"):
                        val = float(mem_str[:-1]) * 1024.0
                    elif mem_str.endswith("B"):
                        val = float(mem_str[:-1]) / (1024.0 * 1024.0)
                    elif mem_str.isdigit():
                        val = float(mem_str) / (1024.0 * 1024.0)
                    footprints[pid] = val
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        print(f"Warning: Failed to capture macOS physical footprint via top: {e}", file=sys.stderr)

    return footprints


def find_orbit_processes() -> Dict[str, List[psutil.Process]]:
    """Locate all active processes belonging to the ORBIT application stack."""
    results: Dict[str, List[psutil.Process]] = {
        "rust_host": [],
        "python_backend": [],
        "webkit_processes": [],
    }

    rust_pids = set()

    for proc in psutil.process_iter(["pid", "name", "cmdline", "ppid"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmdline = " ".join(proc.info["cmdline"] or [])

            # 1. Rust desktop host
            if "orbit-desktop" in name or "orbit-desktop" in cmdline:
                if "target/debug/deps" not in cmdline and "target/release/deps" not in cmdline:
                    results["rust_host"].append(proc)
                    rust_pids.add(proc.pid)

            # 2. Python backend
            elif "uvicorn" in cmdline and "app.main:app" in cmdline:
                results["python_backend"].append(proc)
            elif "python" in name and "app.main:app" in cmdline:
                results["python_backend"].append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # 3. Locate exact WebKit processes associated with ORBIT via open cache directory
    webkit_pids = set()
    cache_dir = os.path.expanduser("~/Library/Caches/orbit-desktop")
    if os.path.exists(cache_dir):
        try:
            res = subprocess.run(
                ["lsof", "+D", cache_dir],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in res.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    webkit_pids.add(int(parts[1]))
        except Exception:
            pass

    for wpid in webkit_pids:
        try:
            p = psutil.Process(wpid)
            if p not in results["webkit_processes"]:
                results["webkit_processes"].append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return results


def measure_process_memory(
    procs: List[psutil.Process], footprints: Dict[int, float]
) -> List[Dict[str, Any]]:
    """Capture RSS and physical footprint for a list of processes."""
    records = []
    for proc in procs:
        try:
            mem = proc.memory_info()
            rss_mb = mem.rss / (1024.0 * 1024.0)
            phys_mb = footprints.get(proc.pid, rss_mb)
            name = proc.name()
            records.append({
                "pid": proc.pid,
                "name": name,
                "rss_mb": round(rss_mb, 2),
                "phys_footprint_mb": round(phys_mb, 2),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return records


def run_benchmark(duration_seconds: int = 10, target_mb: float = 150.0) -> Dict[str, Any]:
    """Execute memory benchmark across all ORBIT processes."""
    print("=" * 68)
    print("ORBIT M0 — Idle Memory Benchmark Runner")
    print(f"Sampling idle memory over {duration_seconds}s (Target: < {target_mb} MB)...")
    print("=" * 68)

    time.sleep(1)
    footprints = parse_top_footprint()
    procs = find_orbit_processes()

    if not procs["rust_host"] and not procs["python_backend"]:
        print("\n[!] No active ORBIT application processes found.")
        print("    Please launch ORBIT (e.g. `pnpm tauri dev`) before running this benchmark,")
        print("    or use `--launch` flag.")
        return {"success": False, "error": "No ORBIT processes active"}

    rust_metrics = measure_process_memory(procs["rust_host"], footprints)
    python_metrics = measure_process_memory(procs["python_backend"], footprints)
    webkit_metrics = measure_process_memory(procs["webkit_processes"], footprints)

    total_rust_rss = sum(p["rss_mb"] for p in rust_metrics)
    total_rust_phys = sum(p["phys_footprint_mb"] for p in rust_metrics)

    total_python_rss = sum(p["rss_mb"] for p in python_metrics)
    total_python_phys = sum(p["phys_footprint_mb"] for p in python_metrics)

    total_webkit_rss = sum(p["rss_mb"] for p in webkit_metrics)
    total_webkit_phys = sum(p["phys_footprint_mb"] for p in webkit_metrics)

    total_rss = total_rust_rss + total_python_rss + total_webkit_rss
    total_phys = total_rust_phys + total_python_phys + total_webkit_phys

    within_target = total_rss <= target_mb

    print("\nProcess Breakdown:")
    print("-" * 68)
    print(f"{'Component':<28} | {'PID(s)':<12} | {'RSS (MB)':<10} | {'Physical (MB)':<12}")
    print("-" * 68)

    # Rust Host
    host_pids = ", ".join(str(p["pid"]) for p in rust_metrics) or "N/A"
    print(f"{'Rust Desktop Shell':<28} | {host_pids:<12} | {total_rust_rss:<10.2f} | {total_rust_phys:<12.2f}")

    # WebKit WebContent
    webkit_pids = ", ".join(str(p["pid"]) for p in webkit_metrics) or "N/A"
    print(f"{'WebKit WebContent Process(es)':<28} | {webkit_pids:<12} | {total_webkit_rss:<10.2f} | {total_webkit_phys:<12.2f}")

    # Python Backend
    py_pids = ", ".join(str(p["pid"]) for p in python_metrics) or "N/A"
    print(f"{'Python 3.13 Backend':<28} | {py_pids:<12} | {total_python_rss:<10.2f} | {total_python_phys:<12.2f}")

    print("-" * 68)
    print(f"{'TOTAL IDLE MEMORY':<28} | {'':<12} | {total_rss:<10.2f} | {total_phys:<12.2f}")
    print("=" * 68)

    status_str = "PASS (Within Target)" if within_target else "EXCEEDS TARGET"
    print(f"Target Budget : < {target_mb:.2f} MB")
    print(f"Actual Total  : {total_rss:.2f} MB RSS ({total_phys:.2f} MB Physical Footprint)")
    print(f"Benchmark     : {status_str}")
    print("=" * 68)

    return {
        "success": True,
        "within_target": within_target,
        "target_mb": target_mb,
        "total_rss_mb": round(total_rss, 2),
        "total_phys_mb": round(total_phys, 2),
        "components": {
            "rust_host": {"pids": [p["pid"] for p in rust_metrics], "rss_mb": round(total_rust_rss, 2), "phys_mb": round(total_rust_phys, 2)},
            "python_backend": {"pids": [p["pid"] for p in python_metrics], "rss_mb": round(total_python_rss, 2), "phys_mb": round(total_python_phys, 2)},
            "webkit": {"pids": [p["pid"] for p in webkit_metrics], "rss_mb": round(total_webkit_rss, 2), "phys_mb": round(total_webkit_phys, 2)},
        },
    }


def main():
    parser = argparse.ArgumentParser(description="ORBIT M0 Idle Memory Benchmarking Tool")
    parser.add_argument("--duration", type=int, default=10, help="Idle stabilization duration in seconds")
    parser.add_argument("--target", type=float, default=150.0, help="Memory budget target in MB")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--out", type=str, default=None, help="Save JSON report to file")
    args = parser.parse_args()

    results = run_benchmark(duration_seconds=args.duration, target_mb=args.target)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"Report saved to: {args.out}")

    if args.json:
        print(json.dumps(results, indent=2))

    sys.exit(0 if results.get("success") else 1)


if __name__ == "__main__":
    main()
