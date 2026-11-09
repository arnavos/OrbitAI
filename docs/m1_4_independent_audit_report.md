# ORBIT Milestone M1.4 Independent Engineering Audit & Reliability Verification Report

---

## 1. Executive Verdict

**VERDICT: FAIL**

While Milestone M1.4 implements foundational relational persistence, monotonic SQLite sequence allocation via `UPDATE ... RETURNING`, and multi-turn context budgeting that passes its existing 215 automated tests, an adversarial independent audit identified **three significant reliability and correctness defects** that violate architectural invariants:

1. **Unterminated Streams Incorrectly Marked Completed (HIGH Severity)**: If an inference stream abruptly terminates without yielding a terminal `finish_reason` (e.g. engine connection drops or network partitions), `ConversationService` falsely records the truncated turn as `status="completed"` with `finish_reason="stop"`. This directly violates the invariant: *"An unterminated stream must never be marked completed."*
2. **Orphaned Assistant Turns in Context History (MEDIUM Severity)**: When historical messages are pruned under memory budget pressure, `ContextManager.assemble_context` iterates messages individually rather than in conversational turn pairs `(user, assistant)`. If an assistant response fits in the remaining budget but its preceding user question does not, the user question is pruned while the assistant response is retained, producing an invalid prompt sequence that starts with an orphaned assistant message.
3. **Unbounded In-Memory Lock Registry Leak (MEDIUM Severity)**: The cleanup logic in `ConversationService._acquire_conversation_lock` evaluates `if not lock.locked()` *inside* the `async with lock:` block where `lock.locked()` is always `True`. As a result, locks are never purged from `_conversation_locks`, causing the lock table to grow unboundedly over the daemon's lifespan.

These defects must be remediated before freezing M1.4 and proceeding to Milestone M1.5 (Streaming Transport & Desktop Chat UI).

---

## 2. Git Integrity and Audit Isolation

- **Branch**: `main`
- **Target Commit**: `db2f6b124a5905183ee224e43e33aee9ecfb2293`
- **Parent Commit**: `9a6dc64668aa915fb84c48570c37acb08b230eed` (M1.3 release checkpoint)
- **Remote Synchronization**: In sync with `origin/main` (`9a6dc64..db2f6b1`).
- **Working Tree State**: Clean. Zero tracked modifications, zero uncommitted staged files, zero untracked files.
- **Audit Isolation**: All adversarial tests and stress benchmarks were executed using isolated in-memory or temporary disk fixtures (`tempfile`) and cleaned up immediately. No tracked files were modified.

---

## 3. Architecture Compliance Audit

The M1.4 implementation was audited against the approved amended architecture specification:

| Architectural Requirement | Implementation Status | Audit Finding |
| :--- | :--- | :--- |
| **Relational Conversation & Message Schema** | Compliant | UUIDv4 PKs, SQLite foreign keys with `ON DELETE CASCADE`. |
| **Monotonic Sequence Allocation** | Compliant | Atomic `UPDATE conversations ... RETURNING last_sequence`. |
| **Zero Database Locks During Inference** | Compliant | Phase 1 commits user turn, Phase 3 commits placeholder, Phase 5 updates terminal state. No DB locks in Phase 4. |
| **Exclusion of Streaming Transport from M1.4** | Compliant | Synchronous REST endpoints only. SSE, WebSockets, and UI streaming deferred to M1.5. |
| **Token Budget Headroom Enforcement** | Compliant | Input budget $C_{\text{alloc}} - M_{\text{out}} - 32$. Prompt non-truncation enforced. |
| **Preservation of Turn Boundaries** | **NON-COMPLIANT** | Pruning can admit assistant response while evicting corresponding user prompt (Finding 2). |
| **Accurate Stream Termination Accounting** | **NON-COMPLIANT** | Streams lacking a terminal finish reason default to `completed/stop` (Finding 1). |
| **Bounded Lock Lifecycle** | **NON-COMPLIANT** | Lock cleanup check is dead code inside `async with lock:` (Finding 3). |
| **Startup Crash Reconciliation** | Compliant | Idempotently transitions `streaming` / `pending` turns to `interrupted/aborted`. |

---

## 4. Database Integrity and Migration Audit

### Schema & Constraints
- **Table `conversations`**: Correctly models thread state, title, default model, and atomic sequence counter (`last_sequence` with server default `0`).
- **Table `messages`**: Correctly models sequential turns with composite unique constraint `uq_messages_conversation_sequence` (`(conversation_id, sequence)`).
- **Foreign Key Enforcement**: Verified active via `PRAGMA foreign_keys = ON;`. Deleting a conversation triggers atomic SQLite cascade deletion of all associated message rows.

### Migration Lifecycle Verification
The Alembic migration `0002_conversations_and_messages.py` was tested across a complete round-trip lifecycle on a temporary SQLite instance:
$$\text{Base} \xrightarrow{\text{upgrade}} 0001 \xrightarrow{\text{upgrade}} 0002 \xrightarrow{\text{downgrade}} 0001 \xrightarrow{\text{downgrade}} \text{Base} \xrightarrow{\text{upgrade}} 0002$$
- M0 system settings and audit events were seeded in `0001` and verified intact after `0002` upgrade.
- Downgrade to `0001` drops `messages` and `conversations` cleanly without orphaned triggers.
- Re-upgrade to `0002` recreates all tables, indexes, and unique constraints without error.

### Sequence Gap Analysis
In `ConversationService.send_message`:
- Phase 1 executes `UPDATE conversations SET last_sequence = last_sequence + 1 ... RETURNING last_sequence` and `INSERT INTO messages` in the **same transaction**. If the transaction fails, both rollback atomically.
- Phase 3 executes the assistant sequence increment and message insertion in the **same transaction**.
- If Phase 2 fails (e.g. `ContextWindowExceededError`), Phase 3 is never reached; `last_sequence` remains equal to the user message sequence ($S=1$). A subsequent user message correctly receives $S=2$.
- Gaps in `sequence` can only occur if a committed turn is manually deleted outside the service layer. Under normal application workflows, sequence numbers are strictly contiguous and monotonic $(1, 2, 3, \dots)$.

---

## 5. Concurrency and Deadlock Audit

### Adversarial Test Results
1. **10 Simultaneous Submissions to ONE Conversation**:
   - In-memory `asyncio.Lock` successfully serialized all 10 turns.
   - Result: 20 sequential messages (10 user + 10 assistant) committed with contiguous, collision-free sequence numbers $1 \dots 20$.
   - Zero `IntegrityError` constraint violations.
2. **10 Simultaneous Submissions to 10 DIFFERENT Conversations**:
   - Requests executed concurrently across distinct thread locks without mutual contention (completed in 166ms total).
3. **Concurrent CRUD Operations**:
   - Interleaved `create`, `update` (rename), `list_conversations`, and `delete_conversation` completed without SQLite `busy_timeout` or deadlocks.

### Lock Table Memory Leak (Finding 3)
In `ConversationService._acquire_conversation_lock`:
```python
async with lock:
    try:
        yield
    finally:
        # Cleanup idle locks if not held
        async with cls._locks_guard:
            if not lock.locked() and conversation_id in cls._conversation_locks:
                cls._conversation_locks.pop(conversation_id, None)
```
Because the `finally:` block resides *within* `async with lock:`, `lock.locked()` is evaluated while the current coroutine holds the lock. Consequently, `lock.locked()` is always `True`, `not lock.locked()` is always `False`, and `_conversation_locks.pop()` is never called. Every conversation thread ever locked permanently leaks an `asyncio.Lock` instance in memory.

### Single-Worker Invariant Verification
The in-memory lock table `_conversation_locks` operates strictly within a single Python process. If multiple Uvicorn workers are spawned, cross-process race conditions would allow simultaneous prompts to execute against the same thread. However, the database-level `UPDATE ... RETURNING` and `UNIQUE(conversation_id, sequence)` constraint prevent database corruption (the second worker would fail with `IntegrityError` rather than silently corrupting sequence ordering).

---

## 6. Async Cancellation and Resource Cleanup Audit

Cancellation was audited at the 7 specified execution points:

1. **Before acquiring conversation lock**: Task cancelled before lock entry. Zero DB side-effects; cancelled cleanly.
2. **While waiting for conversation lock**: Task cancelled while awaiting `lock.acquire()`. Waiter removed from lock queue; existing turn uninterrupted.
3. **After committing user message**: User message committed with `status="completed"`. Assistant placeholder not created. Future turns increment from user sequence cleanly.
4. **During context assembly**: Same as Point 3. User message safely preserved.
5. **After creating assistant placeholder**: Assistant committed with `status="streaming"`. Registry lookup is outside `try/except CancelledError` (Finding 4). If cancelled here, message remains in `streaming` until daemon restart.
6. **During provider inference**: Cancelled while token streaming is active. Caught by `except asyncio.CancelledError:`. Executed shielded `_finalize_assistant_turn`, preserved partial tokens, marked `status="interrupted"`, `finish_reason="cancelled"`, and re-raised `CancelledError`. Verified compliant.
7. **During final persistence**: Shielded update guarantees transaction commits even if parent coroutine receives cancellation.

---

## 7. Context Management and Token Budget Audit

### Invariant Verification
The invariant:
$$\text{estimated\_prompt\_tokens} + \text{max\_output\_tokens} \le \text{allocated\_context} - 32$$
was evaluated across 40 combinations of allocated context ($128 \dots 32768$) and requested output tokens ($100 \dots 5000$). The invariant held strictly in all cases.

### Prompt Non-Truncation
- Prompt exactly at input budget: Admitted without truncation.
- Prompt one token above budget: Rejected with `ContextWindowExceededError` (HTTP 400). User prompt safely committed in Phase 1, never truncated.

### Turn Boundary Pruning Defect (Finding 2)
In `ContextManager.assemble_context`:
```python
for turn in reversed(valid_history):
    turn_cost = cls.estimate_tokens(turn.content)
    if turn_cost <= budget_remaining:
        admitted_history.append(...)
        budget_remaining -= turn_cost
    else:
        break
```
Empirical testing confirmed that when a conversation has history `[Turn 1 User, Turn 1 Asst, Turn 2 User, Turn 2 Asst]` where `Turn 2 Asst` fits in the remaining budget but `Turn 2 User` does not:
- `Turn 2 Asst` is appended to `admitted_history`.
- `Turn 2 User` exceeds budget, triggering `break`.
- `assemble_context` outputs: `[Turn 2 Asst, Latest User Prompt]`.
The prompt sent to the LLM begins with an assistant message without the preceding user question.

### Heuristic Limitation Analysis
The 3.5 character-per-token heuristic (`math.ceil(len(text) / 3.5) + 4`) is safe for Latin prose (~4 chars/token) and code with normal formatting. However:
- **CJK Characters**: A single Chinese/Japanese character often tokenizes to 1–3 BPE tokens. For CJK text, `len(text) / 3.5` severely under-estimates tokens, risking context overflows if long CJK prompts are used.
- **Dense Emojis & Math Symbols**: Multi-byte UTF-8 sequences can exceed 1 token per character.
This is an inherent limitation of zero-dependency character heuristics.

---

## 8. Provider Integration Audit

### Full Lifecycle Trace
`ConversationService.send_message` correctly:
1. Resolves provider and model descriptor from `ModelRegistry`.
2. Translates requested context into Ollama `extra_params={"num_ctx": allocated_context}`.
3. Propagates `max_output_tokens` into `GenerationConfig`.
4. Maps token deltas, usage statistics, and error details.

### Unterminated Stream Defect (Finding 1)
Lines 439–447 in `ConversationService.send_message`:
```python
if final_status != "failed":
    if final_finish_reason in (FinishReason.STOP.value, FinishReason.LENGTH.value):
        final_status = "completed"
    elif final_finish_reason == FinishReason.CANCELLED.value:
        final_status = "interrupted"
    else:
        final_status = "completed"
        final_finish_reason = FinishReason.STOP.value
```
When a mock provider abruptly stopped yielding tokens without sending a `finish_reason` (simulating dropped socket connection), the execution entered the `else` block:
- Result: `assistant_message.status = "completed"`, `finish_reason = "stop"`.
- Truncated text `"Mid sentence cutoff..."` was marked as a complete, successful turn.

---

## 9. Crash Recovery and State Machine Audit

- **State Transitions**:
  - Normal execution: `pending` $\rightarrow$ `streaming` $\rightarrow$ `completed`.
  - Inference failure: `pending` $\rightarrow$ `streaming` $\rightarrow$ `failed`.
  - Client cancellation: `pending` $\rightarrow$ `streaming` $\rightarrow$ `interrupted` (`finish_reason="cancelled"`).
  - Daemon crash / kill: `streaming` / `pending` $\rightarrow$ `interrupted` (`finish_reason="aborted"`).
- **Startup Reconciliation**:
  - `ConversationService.reconcile_crashed_generations(session)` executed against seeded `streaming` and `pending` turns transitioned them to `interrupted/aborted`.
  - Idempotency verified: Running recovery a second time resulted in 0 modified rows.
  - Context isolation verified: Interrupted and failed turns are strictly excluded from subsequent conversation history during `assemble_context`.

---

## 10. API and Security Audit

- **Input Validation**: `SendMessageRequest` validates non-empty content and temperature bounds ($0.0 \le T \le 2.0$).
- **Missing Max Length Limit (Finding 5)**: `content` does not specify `max_length`. Massive strings are persisted to SQLite in Phase 1 before being rejected in Phase 2.
- **SQL Injection**: Prevented by parameterized SQLAlchemy Core and ORM constructs.
- **Information Leakage**: All API responses use normalized Pydantic schemas. Raw database exceptions are caught and wrapped.
- **Privacy & Logging**: Source review verified that prompt text and conversational messages are never written to logger output. Only UUIDs and error codes are logged.
- **Local Isolation**: Endpoints enforce session token authentication (`require_session_auth`) and bind to local interfaces.

---

## 11. Test Suite Credibility & Quality Assessment

### Test Suite Execution
- **Backend Tests**: 215 passed in 3.87s (`PYTHONMALLOC=debug`).
- **Frontend Vitest Suite**: 31 passed in 1.04s.
- **Frontend TypeScript Check**: Passed with 0 errors (`tsc --noEmit`).
- **Frontend Production Build**: Vite build succeeded in 2.12s.
- **Rust Tauri Suite**: 10 passed in 7.38s.
- **Rust Clippy & Format**: Passed with 0 warnings.
- **Unified Runner (`scripts/verify_m0.sh`)**: All 10 verification stages passed in 18s.

### Test Quality Findings
The existing M1.4 test suite is well-structured and uses real SQLite database sessions rather than mocking SQLAlchemy. However:
- **Gap 1**: Tests did not test stream termination without a `finish_reason`, allowing Finding 1 to pass undetected.
- **Gap 2**: Context tests tested uniform message sizes and did not test turn-pair asymmetry where assistant fits but user exceeds budget, allowing Finding 2 to pass undetected.
- **Gap 3**: Concurrency tests asserted that locks worked during active requests, but did not assert that `_conversation_locks` was empty after requests finished, allowing Finding 3 to pass undetected.

---

## 12. Performance and Resource Findings

- **Database Queries Per Turn**: 4 queries total (Phase 1 user insert, Phase 2 history select, Phase 3 assistant placeholder insert, Phase 5 assistant update).
- **History Query Efficiency**: Utilizes compound B-tree index `ix_messages_conversation_sequence` (`(conversation_id, sequence)`). No table scans.
- **Context Assembly Complexity**: Single-pass reverse traversal with early exit ($O(H)$ where $H$ is historical turn count).
- **Lock Contention**: Zero contention across distinct conversation threads.

---

## 13. Audit Findings Ranked by Severity

### Finding 1: Unterminated Stream Falsely Marked Completed (HIGH)
- **Component**: `backend/app/services/conversation_service.py:send_message` (lines 444–446)
- **Reproduction**: Provider yields token chunk and terminates iterator without `delta.finish_reason`.
- **Actual Behavior**: Fallthrough sets `final_status = "completed"` and `final_finish_reason = "stop"`.
- **Expected Behavior**: Must be marked `status = "interrupted"`, `finish_reason = "aborted"`, and raise `AIError("Stream terminated prematurely without finish reason")`.
- **Impact**: Incomplete model responses are treated as valid completed turns.

### Finding 2: History Traversal Admits Orphaned Assistant Turn (MEDIUM)
- **Component**: `backend/app/ai/context.py:ContextManager.assemble_context` (lines 136–150)
- **Reproduction**: Historical Turn 2 has a 20-token assistant message and a 500-token user message. Remaining history budget is 100 tokens.
- **Actual Behavior**: Assistant message is admitted, user message is rejected, resulting in prompt history starting with an orphaned assistant turn.
- **Expected Behavior**: History traversal must evaluate complete `(user, assistant)` turn pairs and discard the assistant message if its corresponding user message cannot fit.
- **Impact**: Generates invalid ChatML/dialogue structures for models expecting strict user/assistant alternation.

### Finding 3: Unbounded Memory Leak in Lock Registry (MEDIUM)
- **Component**: `backend/app/services/conversation_service.py:_acquire_conversation_lock` (lines 62–66)
- **Reproduction**: Call `_acquire_conversation_lock(conv_id)` and inspect `_conversation_locks`.
- **Actual Behavior**: Cleanup executes while holding `lock`, so `not lock.locked()` is always `False`. Entry is never removed.
- **Expected Behavior**: Registry should track reference count or cleanup idle locks safely outside the lock acquisition.
- **Impact**: `_conversation_locks` grows monotonically with every conversation accessed.

### Finding 4: Assistant Message Left in Streaming State on Pre-Inference Cancellation (LOW)
- **Component**: `backend/app/services/conversation_service.py:send_message` (lines 400–415)
- **Reproduction**: Cancel task while `registry.resolve_provider_for_model()` or `registry.get_model()` is executing.
- **Actual Behavior**: Execution aborts before entering the `try: ... except asyncio.CancelledError:` block. Assistant placeholder remains `streaming`.
- **Expected Behavior**: The `try:` block should encompass all operations immediately following the Phase 3 assistant placeholder commit.
- **Impact**: Non-fatal; reconciled on next startup, but leaves dangling turn in active session.

### Finding 5: Missing Request Payload Max Length Constraint (LOW)
- **Component**: `backend/app/schemas/conversation.py:SendMessageRequest`
- **Reproduction**: Submit a 50 MB string in `content`.
- **Actual Behavior**: 50 MB string is committed to SQLite in Phase 1 before Phase 2 context assembly rejects inference.
- **Expected Behavior**: Pydantic schema should enforce `max_length=100_000` (HTTP 422 before DB write).
- **Impact**: Unnecessary SQLite database bloat from unconstrained payloads.

### Finding 6: Character Token Estimation Limitation on Non-Latin Languages (INFORMATIONAL)
- **Component**: `backend/app/ai/context.py:ContextManager.estimate_tokens`
- **Analysis**: 3.5 chars/token heuristic is adequate for English and code, but under-budgets CJK characters and dense emoji strings.

---

## 14. Recommended Remediation Plan

1. **Remediate Stream Termination Logic**:
   In `backend/app/services/conversation_service.py`:
   ```python
   if final_status != "failed":
       if final_finish_reason in (FinishReason.STOP.value, FinishReason.LENGTH.value):
           final_status = "completed"
       elif final_finish_reason == FinishReason.CANCELLED.value:
           final_status = "interrupted"
       elif final_finish_reason is None:
           # Stream terminated without finish reason
           final_status = "interrupted"
           final_finish_reason = "aborted"
           final_error = "Inference stream terminated abruptly without finish reason"
       else:
           final_status = "interrupted"
           final_finish_reason = "aborted"
   ```

2. **Enforce Turn-Pair Atomicity in Context Assembly**:
   In `backend/app/ai/context.py`:
   Group completed history into turn pairs `(user_msg, asst_msg)`. For each pair in reverse:
   If `cost(user_msg) + cost(asst_msg) <= budget_remaining`: admit both.
   Else: halt traversal. Never admit an assistant response without its paired user prompt.

3. **Implement Reference-Counted Lock Registry**:
   In `backend/app/services/conversation_service.py`:
   Maintain `_lock_ref_counts: dict[str, int]`.
   Increment ref count on entry under `_locks_guard`.
   Decrement ref count on exit under `_locks_guard`; if ref count reaches 0, pop `_conversation_locks[conversation_id]`.

4. **Expand Try/Except Scope**:
   In `backend/app/services/conversation_service.py`:
   Move the `try: ... except asyncio.CancelledError:` block to immediately follow Phase 3 placeholder insertion.

5. **Add Pydantic Max Length**:
   In `backend/app/schemas/conversation.py`:
   Add `max_length=100_000` to `SendMessageRequest.content`.

---

## 15. Final Release Recommendation

**M1.4 should not be frozen in its current state.**

The team should execute a focused remediation sprint addressing Findings 1, 2, and 3, add regression tests covering abrupt stream termination and asymmetric turn pruning, and re-run this audit before advancing to M1.5.

