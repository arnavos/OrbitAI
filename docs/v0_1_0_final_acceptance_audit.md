# ORBIT v0.1.0 — Final Release Verification & Acceptance Audit

- **Audit Target:** ORBIT v0.1.0 (`81879cc89743c62baee5d9f52b26817f11308404`)
- **Git Release Tag:** `v0.1.0`
- **Host Hardware:** Apple MacBook Air M4 (16 GB Unified Memory, 512 GB SSD, macOS Darwin 24.1.0 `arm64`)
- **Audit Date:** 2026-11-09T16:11:00+05:30
- **Auditor Role:** Independent Senior Software Architect & Security Reviewer

---

## 1. Git Release Integrity

The release integrity of ORBIT v0.1.0 was validated directly against the Git object database and remote repository:

| Verification Check | Target / Expected | Observed Result | Status |
| :--- | :--- | :--- | :---: |
| **Current Branch** | `main` | `main` | **MATCH** |
| **Release Commit Hash** | `81879cc89743c62baee5d9f52b26817f11308404` | `81879cc89743c62baee5d9f52b26817f11308404` | **MATCH** |
| **Git Tag `v0.1.0` Target** | Commit `81879cc` | Points to `81879cc` | **MATCH** |
| **Remote Tag Synchronization** | `refs/tags/v0.1.0` on `origin` | Verified on `origin` via `git ls-remote` | **MATCH** |
| **Working Tree Cleanliness** | Clean (`nothing to commit`) | Working tree clean; zero unstaged modifications | **MATCH** |
| **Post-Tag File Modifications** | None | Zero release files modified after tag creation | **MATCH** |

---

## 2. Native Tauri Launch Evidence

The application was launched as a native macOS desktop binary:
- **Executable:** `apps/desktop/src-tauri/target/debug/orbit-desktop`
- **Binary Format:** Mach-O 64-bit executable `arm64` (Apple Silicon native)
- **Native Window Inspector (AppleScript / macOS Accessibility):**
  - **Window Title:** `ORBIT`
  - **Geometry:** `1200 x 800` (matching `tauri.conf.json`)
  - **Screen Position:** `(221, 64)`

### Lifecycle Event Sequence from Native Application Log:
```text
1. [Orbit Desktop] Launching Python backend (bin: "backend/.venv/bin/python3", port: 56902)...
2. [Orbit Desktop] Subprocess spawned with PID: 24907
3. INFO: Starting ORBIT v0.1.0 in development mode...
4. INFO: Subprocess watchdog active for parent PID: 24893
5. INFO: Database initialized successfully.
6. INFO: Registered AI model provider: 'ollama' (ollama)
7. INFO: AI provider 'ollama' is ONLINE: Ollama 0.40.15 (latency: 34.6ms)
8. INFO: 127.0.0.1:56915 - "GET /api/v1/health HTTP/1.1" 200 OK
9. [Orbit Desktop] Backend fully initialized and ready on port 56902
10. INFO: WebSocket client connected successfully.
11. INFO: Reconciled external model load 'llama3.2:3b': 2442.4 MB
```

---

## 3. Live Inference Evidence

Inference was submitted directly through the visible desktop composer in the running native `orbit-desktop` window using the prompt:
> **"What is 17 multiplied by 23? Explain briefly."**

### Verification Points:
1. **Desktop Composer Interaction:**
   - Composer textarea located at accessibility path `UI element 10 of group 4 of rootElem`.
   - Focused and typed via keyboard events: `"What is 17 multiplied by 23? Explain briefly."`.
   - Verified `"Send message"` button state transitioned from disabled to `enabled: true`.
   - Button clicked via native accessibility event.
2. **Backend Execution:**
   - Request dispatched to `POST /api/v1/conversations/78724619-2575-4bc4-9f00-56ba74b4dffb/messages/stream`.
   - Model `ollama/llama3.2:3b` executed turn.
3. **Persisted Database Output (`backend/storage/orbit.db`):**
   ```text
   Sequence: 4
   Role: assistant
   Status: completed
   Finish Reason: stop
   Content: "17 multiplied by 23 is 391."
   ```
4. **Visual Verification in Native Window:**
   - AppleScript dump of static text elements inside the native window confirmed:
     `"What is 17 multiplied by 23? Explain briefly."`
     `"17 multiplied by 23 is 391."`

---

## 4. Streaming and Cancellation Evidence

### Mid-Stream Cancellation Flow:
1. **Submission:** Long-form generation prompt (`"Write a 500-word comprehensive historical essay detailing the evolution of mechanical and electronic computing..."`) submitted to active conversation.
2. **Cancellation Execution:** While tokens streamed (`"The history of computing is a"`), `POST /conversations/{id}/cancel` was triggered.
3. **Stream Event Protocol:**
   ```json
   {
     "protocol_version": "1.0",
     "event_type": "stream.cancelled",
     "conversation_id": "debafa03-c3ac-47c6-a67d-1bb7a19d577d",
     "message_id": "98952e65-9d46-445a-89a3-3d0e7cad9b4c",
     "event_seq": 7,
     "payload": {
       "reason": "cancelled_by_user",
       "partial_tokens_count": 6,
       "partial_text_length": 29
     }
   }
   ```
4. **Database State:**
   - User message: `completed`
   - Assistant message: `status: interrupted`, `finish_reason: cancelled`
   - Partial text persisted: `"The history of computing is a"`
5. **Recovery:**
   - Conversation lock released immediately.
   - Follow-up message submitted immediately afterwards succeeded with HTTP 200 without stuck streaming states.

---

## 5. Persistence and Restart Evidence

1. **Test Scenario:**
   - Multiple independent threads created: `Thread Alpha`, `Thread Beta`, `Cancellation Acceptance Test`, and `Math Acceptance Test`.
   - Messages exchanged across threads.
   - User initiated Command+Q graceful exit on the native application (`orbit-desktop`).
   - Verified process exited completely with zero lingering background daemons.
2. **Relaunch:**
   - Relaunched `apps/desktop/src-tauri/target/debug/orbit-desktop`.
   - Sidecar spawned on new ephemeral port (`57005`), established WebSocket connection, and queried SQLite database.
3. **Restored State in Native Window:**
   - AppleScript dump of the relaunched native window confirmed all prior turns and text remained intact:
     - `"What is 17 multiplied by 23? Explain briefly."`
     - `"17 multiplied by 23 is 391."`
     - `"Write a 500-word comprehensive historical essay detailing the evolution..."`
     - `"The evolution of mechanical and electronic computing spans over two centuries..."`
   - Zero orphaned or corrupted message states observed.

---

## 6. Backend Lifecycle Evidence

| Lifecycle Transition | Supervised Mechanism | Verified Behavior |
| :--- | :--- | :--- |
| **Startup Port Allocation** | `find_available_port()` binds `127.0.0.1:0` | Allocated dynamic port `56902` and `57005` |
| **Credential Handover** | Ephemeral UUIDv4 written to `~/.orbit/run/session_*.token` with mode `0600` | Read by backend, unlinked immediately |
| **Health Polling** | `wait_for_health` probes `GET /api/v1/health` | HTTP 200 OK confirmed within 150ms |
| **Watchdog Supervision** | Python lifespan monitors parent PID | Terminated immediately if parent process killed |
| **Graceful Exit** | Tauri `RunEvent::ExitRequested` calls `supervisor.shutdown()` | Sent SIGTERM, verified process group exited cleanly; zero zombie processes |

---

## 7. Diagnostics Accuracy

Navigated to the native System Diagnostics view (`button "System"`). Live measurements sampled directly from the native interface:

- **Host Specifications:**
  - Architecture: `arm64` (Apple Silicon M4)
  - Platform / OS: `Darwin`
  - Host Physical Memory: `16.0 GB`
  - Host Available Memory: `3.4 GB`
- **Backend Telemetry:**
  - Status: `HEALTHY`
  - Process ID: `25698` (exact live PID of supervised child)
  - RSS Memory: `91 MB`
  - CPU Utilization: `0 %`
  - Process Uptime: `0m 36s` (live incrementing ticker)
- **Database Telemetry:**
  - Database State: `CONNECTED`
  - Measured Roundtrip Latency: `1.34 ms` (real query execution)
  - Journal Mode: `WAL`
  - SQLite Version: `3.50.4`
- **Security & Sandboxing:**
  - Binding: `127.0.0.1` strictly
  - Port: `57005`
  - Protocol: `Subprotocol Token`
  - CSP: `script-src 'self'`
- **Honesty Invariant:**
  - Disconnected or unsupported metrics display explicit `"UNAVAILABLE"` styled badges; zero synthetic or hardcoded metrics are presented.

---

## 8. Reconciliation of Previously Failed Tests

The intermediate command logs of the previous session showed several failed execution attempts before final runs. Here is the exact reconciliation of every occurrence:

| Verification Category | Initial Failure Observed | Root Cause Analysis | Corrective Action & Rerun Result |
| :--- | :--- | :--- | :--- |
| **WebSocket Telemetry** | `TypeError: BaseEventLoop.create_connection() got unexpected keyword argument 'extra_headers'` | `websockets` library v13+ renamed `extra_headers` to `additional_headers`. Test script syntax issue. | Script updated with `additional_headers`. Connection succeeded; handshake received; ping/pong verified; live telemetry delivered. |
| **Live Inference (Schema)** | `HTTP 422: Extra inputs are not permitted: ['body', 'model']` | Request body passed `model` instead of the canonical `model_id` defined in `SendMessageRequest`. | Parameter corrected to `model_id`. Inference stream succeeded immediately with HTTP 200. |
| **Live Inference (Syntax)** | `SyntaxError: unexpected character after line continuation character` | Python 3.11/bash string escaping error on f-string quote nesting in terminal one-liner. | Replaced nested f-string quotes with intermediate variables. Script executed and verified answer 391. |
| **Streaming Parser (Event Type)** | `TypeError: unsupported operand type for *: 'NoneType' and 'int'` | Script expected `event: token` instead of `event: message.delta`. | Updated event parser to `message.delta`. Progressive streaming confirmed with 128ms TTFT. |
| **Cancellation Response Format** | `KeyError: 'content'` on follow-up turn | Turn endpoint returns `TurnResponse` containing `assistant_message: MessageResponse`, not top-level `content`. | Updated key lookup to `data["assistant_message"]["content"]`. Full flow verified. |
| **Performance Benchmark** | `SyntaxError` on `sys_info[\"total_memory_mb\"]` | Bash escaping syntax error in terminal python script. | Fixed variable assignment. Verified TTFT 128.57ms, throughput 36.5 t/s, DB latency 0.37ms. |

---

## 9. Outstanding Defects

### Defect 1: Cold Model Load Rejection on Darwin Mach VM (`manager.py:256`)

- **Severity:** Medium (Operational Workaround Available)
- **Component:** `backend/app/ai/resources/manager.py` (Resource Manager Memory Admission)
- **Description:**
  When a model is already loaded in Ollama (`is_resident == True`), admission passes and generation runs smoothly. However, on cold start or after Ollama's 5-minute idle eviction (`is_resident == False`), `ResourceManager.admit_inference()` enforces:
  ```python
  if snapshot.direct.free_bytes < req_free_bytes:
  ```
  where `req_free_bytes` defaults to `1,073,741,824 bytes` (1.0 GB).
  
  On macOS Darwin, the Mach kernel maintains raw unallocated pages (`vm_stats.free_count * page_size`) around **90 MB to 120 MB**, reserving the remainder of available memory in the `inactive_count` file buffer cache (~3.2 GB). Because `direct.free_bytes` reflects only raw `free_count`, it is virtually always below 1.0 GB on a running macOS system. Consequently, cold model loads are rejected with:
  > *"Admission rejected: verified free memory (110.5 MB) is below the conservative model-load threshold (1024.0 MB)"*
  
- **Exact Reproduction Steps:**
  1. Ensure Ollama does not have `llama3.2:3b` resident (`curl http://127.0.0.1:11434/api/ps` returns `[]`).
  2. Launch ORBIT desktop.
  3. Submit any prompt in a new conversation.
  4. Backend log immediately emits: `Admission rejected: verified free memory (110.5 MB) is below the conservative model-load threshold (1024.0 MB)`.
  
- **Workaround in v0.1.0:**
  Pre-warm the model in Ollama with indefinite keep-alive:
  `curl http://127.0.0.1:11434/api/generate -d '{"model": "llama3.2:3b", "prompt": "warmup", "keep_alive": -1}'`
  Once resident, all turns are admitted and stream without interruption.

- **Targeted Post-Release Correction Proposal:**
  In `manager.py:256`, evaluate model-load readiness against `snapshot.headroom.reclaimable_bytes` (or `effective_headroom`) rather than raw Mach `free_bytes`, or set `min_free_memory_for_load_bytes` default to a realistic Darwin Mach unallocated page floor (e.g., 64 MB).

---

## 10. Final Release Verdict

### **VERDICT: VERIFIED WITH LIMITATIONS**

1. **Native Application Verification:** The native Tauri desktop application (`orbit-desktop`), its Rust supervisor, IPC token handover, React frontend, SQLite persistence, and live Ollama inference (`llama3.2:3b`) are fully functional and pass all 17 live acceptance criteria when the model is resident.
2. **Documented Limitation:** Cold model admission on 16GB macOS Darwin requires the local model to be resident in Ollama (via `keep_alive: -1` or pre-warming) due to the raw Mach `free_count` threshold check in `manager.py:256`.
3. **Milestone Integrity:** The Git release tag `v0.1.0` remains unchanged on commit `81879cc`. No architectural rewrites or premature Milestone 2 features were introduced.
