# ORBIT v0.1.0 — Post-Release Resource Admission Hotfix Report

**Date:** 2026-11-09  
**Host Architecture:** Apple MacBook Air M4 (16GB unified memory, 512GB SSD)  
**Host Kernel:** Darwin 24.1.0 (macOS Sequoia)  
**Release Baseline:** `81879cc89743c62baee5d9f52b26817f11308404` (tag: `v0.1.0`)  
**Audit Report Parent:** `2d1b053` (`docs/v0_1_0_final_acceptance_audit.md`)  
**Status:** **HOTFIX VERIFIED & COMMITTED**

---

## 1. Executive Summary

Following the native desktop acceptance audit for ORBIT v0.1.0, a confirmed platform defect was identified in cold model admission on Darwin/macOS. When local AI models (`llama3.2:3b`) were cold-started or evicted after Ollama's 5-minute idle timeout, subsequent inference requests were erroneously rejected:

```text
Admission rejected: verified free memory (110.5 MB) is below the conservative model-load threshold (1024.0 MB)
```

This occurred despite the Apple Silicon host possessing over 3.3 GB of verified reclaimable memory headroom.

The defect has been resolved by evaluating cold model admission against verified reclaimable headroom (`effective_headroom`) rather than Mach kernel raw unallocated zeroed pages (`snapshot.direct.free_bytes`). All resource safety invariants—including conservative handling of unknown telemetry, OS safety reserve enforcement (1.0 GB floor), resident model accounting, and reservation lifecycle—are strictly preserved.

### Verification Highlights

- **Unit & Regression Tests:** 48/48 resource tests passed (including 5 new Darwin-specific cold-admission regression tests).
- **Backend Test Suite:** 268/268 pytest tests passed in 4.92s.
- **Frontend Test Suite:** 83/83 Vitest component tests passed in 1.54s.
- **Rust Test Suite:** 10/10 cargo tests passed in 7.44s.
- **Unified Verification:** 10/10 M0 verification stages passed cleanly.
- **Live Cold-Load Verification:** Verified twice on live M4 Apple Silicon hardware with `llama3.2:3b` unloaded via Ollama lifecycle (`keep_alive: 0`):
  - **Cold Load Run 1:** Admitted, TTFT 987.92ms, duration 4.07s, 142 tokens streamed.
  - **Cold Load Run 2 (Repeatability):** Admitted, TTFT 988.21ms, duration 1.15s, 8 tokens streamed.
  - **Memory Telemetry:** Verified Mach direct free at 82.1 MB while reclaimable headroom remained at 2,286.6 MB.

---

## 2. Root Cause Analysis

### Mach VM Page Management on Darwin

On Apple Silicon Darwin (macOS), the Mach virtual memory subsystem (`vm_statistics64_data_t`) tracks memory pages across several queues:
- `free_count`: Raw unallocated, zero-filled pages.
- `active_count`: Pages currently bound to active processes.
- `inactive_count`: Pages holding previously read file caches and clean memory.
- `purgeable_count`: Volatile allocations that can be reclaimed instantly without paging.
- `wire_count`: Non-pageable kernel pages.

Under Darwin's unified memory architecture, macOS actively maximizes memory utilization by retaining file caches in `inactive` pages rather than keeping pages in `free_count`. As a result, `vm_statistics64.free_count * page_size` (`snapshot.direct.free_bytes`) hovers continuously between **70 MB and 120 MB**, even when the system has 4+ GB of instantly reclaimable memory available.

### The Invalidation Defect in `manager.py`

In `backend/app/ai/resources/manager.py` (lines 230–314), Step 4 of `_evaluate_admission_under_lock` evaluated:

```python
# DEFECTIVE IMPLEMENTATION:
if not is_resident:
    req_free_bytes = self.policy.min_free_memory_for_load_bytes  # 1.0 GiB
    ...
    if snapshot.direct.free_bytes < req_free_bytes:
        ...
        return AdmissionDecision(
            outcome=AdmissionOutcome.REJECT,
            reason=f"Admission rejected: verified free memory ({free_mb:.1f} MB) is below the conservative model-load threshold ({req_free_mb:.1f} MB)"
        )
```

Because `snapshot.direct.free_bytes` (Mach unallocated zeroed pages) on macOS is almost always ~80–110 MB (< 1,024 MB), any cold model load—whether at application start or after Ollama's automatic 5-minute idle eviction—was unconditionally rejected if no eligible resident model could be evicted.

Already-resident models bypassed Step 4 (`if not is_resident:`) and succeeded. However, as soon as Ollama evicted an idle model, the model could not be loaded again through ORBIT without an external warm-up prompt.

---

## 3. Implementation Details

### Corrected Admission Invariant

The authoritative metric for verified available memory on Darwin is `effective_headroom`, computed as:

$$\text{effective\_headroom} = \max(0, \text{effective\_host\_headroom\_bytes} - \text{pending\_total})$$

where `effective_host_headroom_bytes` in `MacOSMemoryMonitor.compute_derived_headroom()` enforces:
1. **Reclaimable calculation:**
   $$\text{reclaimable} = \text{free} + \text{purgeable} + (\text{inactive\_reclaim\_factor} \times \max(0, \text{inactive} - \text{purgeable}))$$
2. **OS Safety Reserve Floor:** Deducts `policy.os_safety_reserve_bytes` (1.0 GB) under normal pressure.
3. **Kernel Memory Pressure Gating:** Disables headroom (clamps to 0) under `CRITICAL` pressure and discounts by 50% under `WARNING` pressure.
4. **Pending Reservations:** Subtracts active uncommitted cold load reservations to prevent oversubscription.

### Changes to `backend/app/ai/resources/manager.py`

1. **Step 4 Cold Load Admission:**
   Replaced `snapshot.direct.free_bytes < req_free_bytes` with `effective_headroom < req_free_bytes`.
2. **Conservative Handling of Degraded / Unknown Telemetry:**
   Added `or snapshot.derived.is_conservative` to Step 4 and Phase 3 post-unload validation, ensuring that unobservable telemetry, closed Mach ports, or `UNKNOWN` pressure levels fail closed immediately.
3. **Informative Reason Strings:**
   Updated diagnostic strings to state `verified available headroom ({avail_mb:.1f} MB)` instead of misleading raw free memory.

#### Code Diff Summary

```diff
--- a/backend/app/ai/resources/manager.py
+++ b/backend/app/ai/resources/manager.py
@@ -227,12 +227,16 @@ class ResourceManager:
 
         total_req_bytes = pending_base_needed + incremental_needed
 
-        # 4. Conservative model-load verification: require minimum verified free host memory
+        # 4. Conservative model-load verification: require minimum verified available headroom
         if not is_resident:
             req_free_bytes = self.policy.min_free_memory_for_load_bytes
             req_free_mb = req_free_bytes / (1024**2)
 
-            if not snapshot.direct.is_available or snapshot.direct.free_bytes is None:
+            if (
+                not snapshot.direct.is_available
+                or snapshot.direct.free_bytes is None
+                or snapshot.derived.is_conservative
+            ):
                 diag = (
                     f": {snapshot.direct.diagnostic_message}"
                     if snapshot.direct.diagnostic_message
@@ -253,8 +257,8 @@ class ResourceManager:
                     evaluated_at=now,
                 )
 
-            if snapshot.direct.free_bytes < req_free_bytes:
-                free_mb = snapshot.direct.free_bytes / (1024**2)
+            if effective_headroom < req_free_bytes:
+                avail_mb = effective_headroom / (1024**2)
 
                 # Identify an eligible idle resident model as a provisional eviction candidate
@@ -272,7 +276,7 @@ class ResourceManager:
                         available_headroom_bytes=effective_headroom,
                         models_to_unload=[eviction_candidate],
                         reason=(
-                            f"Verified free memory ({free_mb:.1f} MB) is below "
+                            f"Verified available headroom ({avail_mb:.1f} MB) is below "
                             f"{req_free_mb:.1f} MB threshold; "
@@ -292,7 +296,7 @@ class ResourceManager:
                         reason=(
-                            f"Verified free memory ({free_mb:.1f} MB) is below threshold "
+                            f"Verified available headroom ({avail_mb:.1f} MB) is below threshold "
                             f"({req_free_mb:.1f} MB); deferring while active leases finish"
                         ),
@@ -306,8 +310,8 @@ class ResourceManager:
                     reason=(
-                        f"Admission rejected: verified free memory ({free_mb:.1f} MB) is below the "
-                        f"conservative model-load threshold ({req_free_mb:.1f} MB)"
+                        f"Admission rejected: verified available headroom ({avail_mb:.1f} MB) "
+                        f"is below the conservative model-load threshold ({req_free_mb:.1f} MB)"
                     ),
@@ -590,7 +594,9 @@ class ResourceManager:
                 > self.policy.snapshot_freshness_seconds
             )
             is_unavailable = (
-                not snapshot_post.direct.is_available or snapshot_post.direct.free_bytes is None
+                not snapshot_post.direct.is_available
+                or snapshot_post.direct.free_bytes is None
+                or snapshot_post.derived.is_conservative
             )
```

---

## 4. Regression Test Suite

All 48 unit and integration tests in `tests/backend/test_resource_manager.py` and `tests/backend/test_resource_monitor.py` passed cleanly.

### Dedicated Test Coverage for Hotfix Scenarios

| Test Case | Scenario Verified | Outcome |
| :--- | :--- | :--- |
| `test_darwin_cold_load_admitted_with_low_raw_free_pages_and_sufficient_reclaimable_headroom` | Direct free 90 MB, inactive cache 5.5 GB, reclaimable 4.0 GB, headroom 3.0 GB (> 2.3 GB for 3B load). | **PASSED** (`ALLOW`) |
| `test_darwin_cold_load_rejected_with_low_raw_free_pages_and_insufficient_reclaimable_headroom` | Direct free 90 MB, inactive cache 800 MB, reclaimable 700 MB, reserve 1.0 GB -> headroom 0 MB. | **PASSED** (`REJECT`) |
| `test_darwin_cold_load_rejected_when_os_reserve_exceeds_reclaimable` | Reclaimable 1.5 GB, reserve 1.0 GB -> headroom 500 MB (< 1.0 GiB threshold). OS reserve enforced. | **PASSED** (`REJECT`) |
| `test_darwin_cold_load_rejected_on_unknown_telemetry_or_pressure` | Kernel pressure level `UNKNOWN`, `is_conservative=True` -> fails closed immediately. | **PASSED** (`REJECT`) |
| `test_multiple_cold_load_reservations_deduct_without_double_counting` | Two concurrent admissions reserve base budget and properly decrement headroom; 3rd load defers. | **PASSED** (`DEFER`) |
| `test_admission_admitted_when_fresh_raw_free_is_low_but_reclaimable_headroom_sufficient` | Two-phase eviction where post-unload free is 90 MB but reclaimable headroom is 3.5 GB. | **PASSED** (`ALLOW`) |
| `test_admission_rejected_when_fresh_headroom_remains_below_1_gib` | Post-unload headroom remains 900 MB (< 1.0 GiB threshold). | **PASSED** (`REJECT`) |
| `test_admission_rejected_when_fresh_headroom_passes_1_gib_threshold_but_model_does_not_fit` | Post-unload headroom is 1.5 GB (passes 1.0 GiB load threshold, but < 2.3 GB model requirement). | **PASSED** (`REJECT`) |
| `test_already_resident_model_admitted_with_low_free_memory` | Resident model admitted using only scratch buffer (~100 MB) without double-counting base weights. | **PASSED** (`ALLOW`) |
| `test_conservative_min_free_memory_unavailable_telemetry` | Mach host port closed, `is_available=False` -> rejected with diagnostic message. | **PASSED** (`REJECT`) |

---

## 5. Live Verification on M4 Apple Silicon Hardware

The live verification protocol was executed strictly against the native macOS runtime on Apple Silicon M4 with the live Ollama daemon (Ollama 0.40.15) and live SQLite database. Zero synthetic or mocked providers were used.

### Step-by-Step Execution Record

#### 1. Baseline Resident Inference
- **Initial State:** `llama3.2:3b` resident in Ollama (`size_vram: 2561000078`, context 4096).
- **Prompt:** `"What is the capital of France? Answer in one word."`
- **Output:** `"Paris."`
- **Latency / TTFT:** 160.68 ms.
- **Outcome:** **PASSED**

#### 2. Eviction via Supported Ollama Lifecycle
- **Command:** `curl -s http://127.0.0.1:11434/api/generate -d '{"model": "llama3.2:3b", "keep_alive": 0}'`
- **Daemon Response:** `{"model":"llama3.2:3b","created_at":"2026-11-09T11:09:40.915059Z","response":"","done":true,"done_reason":"unload"}`
- **Verification (`/api/ps`):** `{"models": []}`
- **Outcome:** **PASSED — Model fully evicted from unified memory**

#### 3. Real Cold Model Load (Run 1)
- **Host Telemetry at Cold Admission:**
  - Mach Direct Free: `82.1 MB`
  - Inactive File Cache: `4,568.3 MB`
  - Verified Reclaimable: `3,310.6 MB`
  - Verified Headroom: `2,286.6 MB`
  - OS Safety Reserve Floor: `1,024.0 MB`
- **Prompt:** `"Explain the difference between a process and a thread in 2 bullet points."`
- **Backend Log:**
  - `Reconciled external model eviction for 'llama3.2:3b'`
  - `Model 'llama3.2:3b' confirmed resident: 2369.1 MB at context 4096`
- **Performance:**
  - **TTFT (including disk-to-unified-memory cold load):** **987.92 ms**
  - **Total Duration:** 4,071.64 ms
  - **Tokens Generated:** 142 tokens
  - **Finish Reason:** `stop`
- **Response Content:**
  > * A **process** is an independent unit of execution that runs on a computer's hardware, managing its own memory, resources, and memory space...
  > * A **thread**, on the other hand, is a lightweight and efficient way to execute multiple instructions within a process. Threads share the same memory space...
- **Outcome:** **PASSED — Cold model loaded through normal admission with raw free at 82.1 MB**

#### 4. Repeatability Verification (Run 2)
- **Second Eviction:** `curl -s http://127.0.0.1:11434/api/generate -d '{"model": "llama3.2:3b", "keep_alive": 0}'`
- **Verification (`/api/ps`):** `{"models": []}` confirmed.
- **Prompt:** `"What are the three primary colors in traditional pigment mixing? List them concisely."`
- **Performance:**
  - **TTFT (including second cold load):** **988.21 ms**
  - **Total Duration:** 1,145.33 ms
  - **Tokens Generated:** 8 tokens
  - **Finish Reason:** `stop`
- **Response Content:** `"Red, Yellow, and Blue."`
- **Outcome:** **PASSED — 100% repeatable cold load admission**

#### 5. Telemetry Integrity Check
- **Endpoint:** `GET /api/v1/health/detailed`
  - Status: `healthy`
  - Memory RSS: `89.5 MB`
  - Database Latency: `0.38 ms` (WAL mode)
- **Host Memory Monitor:**
  - `snap.direct.is_available`: `True`
  - `snap.derived.is_conservative`: `False`
  - `pressure_level`: `normal`
- **Outcome:** **PASSED — Telemetry valid and uncorrupted**

---

## 6. M0 Verification Runner (10/10 Stages)

```text
======================================================================
                   M0 VERIFICATION SUMMARY REPORT                     
======================================================================
Verification Stage                               | Duration   | Result    
----------------------------------------------------------------------
OpenAPI TypeScript Contract Drift                | 1s         | PASSED    
Python Linting (Ruff Check)                      | 0s         | PASSED    
Python Formatting (Ruff Format)                  | 0s         | PASSED    
Clean-State Database Migration (Alembic)         | 1s         | PASSED    
Backend Pytest Suite (FastAPI & SQLite)          | 3s         | PASSED    
Frontend TypeScript Checks (tsc --noEmit)        | 2s         | PASSED    
Frontend Vitest Suite (React Components)         | 1s         | PASSED    
Frontend Production Build (Vite Build)           | 2s         | PASSED    
Rust Formatting & Clippy (Tauri Desktop)         | 1s         | PASSED    
Rust Unit & Supervisor Tests (Cargo Test)        | 8s         | PASSED    
----------------------------------------------------------------------
Total Execution Time: 19 seconds

✓ ALL M0 VERIFICATION STAGES PASSED! Milestone M0 criteria satisfied.
```

---

## 7. Release Integrity & Git Status

- **Locked Release Tag:** `v0.1.0` remains immutably fixed at commit `81879cc89743c62baee5d9f52b26817f11308404`. No tag was deleted, moved, or recreated.
- **Audit Report Commit:** `2d1b053` on `main`.
- **Maintenance Commit:** Dedicated maintenance commit on `main`.
- **Files Modified:**
  - `backend/app/ai/resources/manager.py` (Hotfix implementation)
  - `tests/backend/test_resource_manager.py` (Regression tests)
  - `docs/v0_1_0_resource_admission_hotfix.md` (This document)

---

## 8. Conclusion

The Darwin cold-model admission defect is completely remediated. ORBIT v0.1.0 now reliably admits cold model loads on macOS with typical Mach VM free-page counts (~80–120 MB), correctly leveraging unified memory inactive file caches while upholding strict safety reserves and memory pressure safeguards.
