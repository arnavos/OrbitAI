# ORBIT Milestone M1.4 Audit Remediation & Reliability Hardening Report

---

## 1. Executive Summary

Milestone **M1.4: Conversation Persistence & Context Management** has undergone targeted remediation and comprehensive hardening to resolve all three correctness and reliability defects identified in `docs/m1_4_independent_audit_report.md`.

All three findings have been resolved with mathematical and concurrency rigor:
1. **Finding 1 (HIGH - Stream Termination Validation)**: Explicit stream completion validation is now enforced. The assistant message is marked `completed` strictly when the provider yields an explicit terminal finish reason (`FinishReason.STOP` or `FinishReason.LENGTH`). Premature stream cutoffs, empty streams, or missing terminal finish reasons are reliably trapped, persisted as `failed` with `finish_reason="error"`, and surface `AIError` to the caller while preserving the committed user prompt.
2. **Finding 2 (MEDIUM - Turn Boundary Atomicity)**: ContextManager now pairs completed historical messages into atomic `(user, assistant)` turn pairs and evaluates budget headroom at the turn-pair level. An assistant message is never admitted without its corresponding user prompt, preventing orphaned assistant turns in model prompt assembly.
3. **Finding 3 (MEDIUM - Reference-Counted Lock Lifecycle)**: Replaced the flawed in-lock cleanup check with a robust reference-counted lock registry (`_LockEntry` tracking `lock` and `ref_count`). Active and queued requests share the lock instance; when all requests complete or are cancelled, the registry entry is safely and atomically pruned without races or memory leaks.

---

## 2. Root Cause Analysis

### Finding 1: Unterminated Inference Streams Incorrectly Marked Completed
- **Root Cause**: `ConversationService.send_message` contained a fallback `else` branch in stream evaluation:
  ```python
  if final_finish_reason in (FinishReason.STOP.value, FinishReason.LENGTH.value):
      final_status = "completed"
  elif final_finish_reason == FinishReason.CANCELLED.value:
      final_status = "interrupted"
  else:
      final_status = "completed"
      final_finish_reason = FinishReason.STOP.value
  ```
  When a stream abruptly terminated without yielding a terminal finish reason (e.g. network partition or daemon crash), `final_finish_reason` remained `None`, falling into the `else` block which falsely classified the truncated turn as `completed` with `finish_reason="stop"`.

### Finding 2: Orphaned Assistant Messages in Assembled Context
- **Root Cause**: `ContextManager.assemble_context` iterated historical messages individually in reverse:
  ```python
  for turn in reversed(valid_history):
      turn_cost = cls.estimate_tokens(turn.content)
      if turn_cost <= budget_remaining:
          admitted_history.append(...)
          budget_remaining -= turn_cost
      else:
          break
  ```
  If an assistant response fit within `budget_remaining` but its preceding user prompt exceeded it, the assistant response was admitted while the user prompt was pruned. When reversed back to chronological order, the prompt began with an orphaned assistant message.

### Finding 3: Conversation Lock Registry Grows Without Bound
- **Root Cause**: In `_acquire_conversation_lock`:
  ```python
  async with lock:
      try:
          yield
      finally:
          async with cls._locks_guard:
              if not lock.locked() and conversation_id in cls._conversation_locks:
                  cls._conversation_locks.pop(conversation_id, None)
  ```
  The `finally:` block was executed *inside* the `async with lock:` block. While inside `async with lock:`, `lock.locked()` is always `True`. Therefore, `not lock.locked()` was always `False`, and `cls._conversation_locks.pop()` was dead code.

---

## 3. Files Changed

1. `backend/app/services/conversation_service.py`:
   - Implemented `_LockEntry` reference-counted lock container.
   - Refactored `_acquire_conversation_lock` to track entry ref-counts and evict idle locks safely upon last reference drop (with cancellation shielding).
   - Moved Phase 4 `try:` block to encompass model/provider resolution.
   - Enforced explicit stream termination validation: missing finish reasons produce `status="failed"` and `finish_reason="error"`.
2. `backend/app/ai/context.py`:
   - Updated `assemble_context` to group completed history into `(user, assistant)` turn pairs.
   - Implemented pair-level budget evaluation and atomic turn-pair admission, preventing orphaned assistant turns.
3. `tests/backend/test_context_manager.py`:
   - Updated `test_failed_and_interrupted_turns_excluded_from_context` to verify turn-pair atomicity.
   - Added `test_turn_pair_atomicity_and_budget_bounds`.
   - Added `test_large_latest_user_prompt_preserved_without_truncation`.
4. `tests/backend/test_conversation_concurrency.py`:
   - Added `MockFastProvider` display name support.
   - Added `test_lock_registry_cleanup_after_completion`.
   - Added `test_lock_registry_ref_counts_and_concurrency`.
   - Added `test_lock_registry_cancellation_while_waiting`.
   - Added `test_lock_registry_cancellation_while_holding`.
   - Added `test_lock_registry_thousands_of_conversations_bounded_memory`.
5. `tests/backend/test_conversation_execution.py`:
   - Added `MockCustomStreamProvider`.
   - Added `test_stream_terminating_without_finish_reason_marked_failed`.
   - Added `test_empty_stream_marked_failed`.
   - Added `test_stream_explicit_stop_finish_reason`.
   - Added `test_stream_explicit_length_finish_reason`.
   - Added `test_stream_terminal_event_followed_by_cleanup`.

---

## 4. Implementation Details

### Stream Termination Validation
In `ConversationService.send_message`:
```python
if final_status != "failed":
    if final_finish_reason in (FinishReason.STOP.value, FinishReason.LENGTH.value):
        final_status = "completed"
    elif final_finish_reason == FinishReason.CANCELLED.value:
        final_status = "interrupted"
    elif final_finish_reason is None:
        # Stream terminated prematurely without terminal finish reason
        final_status = "failed"
        final_finish_reason = FinishReason.ERROR.value
        final_error = "Inference stream terminated prematurely without terminal finish reason"
    else:
        final_status = "failed"
        final_finish_reason = FinishReason.ERROR.value
        final_error = f"Inference stream terminated with unsupported finish reason: {final_finish_reason}"
```

### Turn-Pair Context Assembly
In `ContextManager.assemble_context`:
```python
turn_pairs: list[tuple[Message, Message]] = []
i = 0
while i < len(valid_history) - 1:
    if valid_history[i].role == "user" and valid_history[i + 1].role == "assistant":
        turn_pairs.append((valid_history[i], valid_history[i + 1]))
        i += 2
    else:
        i += 1

admitted_turn_pairs: list[tuple[Message, Message]] = []
for user_turn, asst_turn in reversed(turn_pairs):
    turn_cost = cls.estimate_tokens(user_turn.content) + cls.estimate_tokens(asst_turn.content)
    if turn_cost <= budget_remaining:
        admitted_turn_pairs.append((user_turn, asst_turn))
        budget_remaining -= turn_cost
    else:
        break
```

### Reference-Counted Lock Lifecycle
In `ConversationService._acquire_conversation_lock`:
```python
async with cls._locks_guard:
    if conversation_id not in cls._lock_entries:
        cls._lock_entries[conversation_id] = _LockEntry()
    entry = cls._lock_entries[conversation_id]
    entry.ref_count += 1

lock_acquired = False
try:
    await entry.lock.acquire()
    lock_acquired = True
    yield
finally:
    if lock_acquired:
        entry.lock.release()

    async def _cleanup() -> None:
        async with cls._locks_guard:
            entry.ref_count -= 1
            if entry.ref_count == 0 and cls._lock_entries.get(conversation_id) is entry:
                cls._lock_entries.pop(conversation_id, None)

    await asyncio.shield(_cleanup())
```

---

## 5. New Regression Tests

1. `test_stream_terminating_without_finish_reason_marked_failed`: Proves unterminated token streams persist as `failed` with `finish_reason="error"`.
2. `test_empty_stream_marked_failed`: Proves empty streams without tokens or finish reasons persist as `failed`.
3. `test_stream_explicit_stop_finish_reason`: Proves explicit STOP persists as `completed`.
4. `test_stream_explicit_length_finish_reason`: Proves explicit LENGTH persists as `completed`.
5. `test_stream_terminal_event_followed_by_cleanup`: Proves stream cleanup executes reliably on completion.
6. `test_turn_pair_atomicity_and_budget_bounds`: Proves assistant response is never admitted without paired user prompt.
7. `test_large_latest_user_prompt_preserved_without_truncation`: Proves latest user prompt is never truncated even when history is evicted.
8. `test_lock_registry_cleanup_after_completion`: Proves lock entries are evicted upon request completion.
9. `test_lock_registry_ref_counts_and_concurrency`: Proves waiting requests increment reference count, share lock instance, and clean up.
10. `test_lock_registry_cancellation_while_waiting`: Proves cancelled waiters decrement ref-count without releasing unowned lock.
11. `test_lock_registry_cancellation_while_holding`: Proves cancelled lock holders release lock and decrement ref-count.
12. `test_lock_registry_thousands_of_conversations_bounded_memory`: Proves 1,000 distinct conversations leave 0 leaked lock entries.

---

## 6. Verification Commands & Test Results

| Command | Target | Outcome | Duration |
| :--- | :--- | :--- | :--- |
| `cd backend && uv run pytest -v ../tests/backend/test_*.py` | Targeted M1.4 tests | **28/28 PASSED** | 0.50s |
| `cd backend && PYTHONMALLOC=debug uv run pytest -v ../tests/backend` | Full backend suite | **227/227 PASSED** | 2.16s |
| `pnpm --filter @orbit/frontend test` | Frontend Vitest suite | **31/31 PASSED** | 0.95s |
| `pnpm --filter @orbit/frontend lint` | Frontend TypeScript checks | **PASSED** | 1.00s |
| `pnpm --filter @orbit/frontend build` | Frontend production Vite build | **PASSED** | 0.67s |
| `cd apps/desktop/src-tauri && cargo test` | Rust unit & supervisor tests | **10/10 PASSED** | 7.69s |
| `cd apps/desktop/src-tauri && cargo fmt --check` | Rust formatting | **PASSED** | 0.10s |
| `cd apps/desktop/src-tauri && cargo clippy -- -D warnings` | Rust Clippy | **PASSED** | 0.38s |
| `cd backend && uv run ruff check . ../tests/backend` | Python linting | **PASSED (0 errors)** | 0.40s |
| `cd backend && uv run ruff format --check . ../tests/backend` | Python formatting | **PASSED** | 0.20s |
| `bash scripts/verify_m0.sh` | 10-step unified verification gate | **ALL 10 PASSED** | 18s |

---

## 7. Limitations & Unresolved Concerns

1. **Character Heuristic for Token Estimation**: The 3.5 characters per token estimate (`math.ceil(len / 3.5) + 4`) is safe for Latin alphabets and standard programming languages. For CJK text (where single characters frequently decompose into 1–3 BPE tokens) or dense emoji sequences, token counts can exceed character length. This is an accepted local-first design trade-off to maintain zero heavy tokenizer dependencies in M1.4.
2. **Single-Worker Invariant**: The reference-counted in-memory lock table relies on the documented single-backend-worker architecture. In the event of multi-worker backend deployment, cross-process serialization would require a distributed lock (e.g. SQLite lock or file lock). However, the database-level `UPDATE ... RETURNING` and unique constraint prevent database corruption in all scenarios.

---

## 8. Final Recommendation

**Milestone M1.4 is now fully remediated, verified, robust, and READY TO FREEZE.**

All three defects from the independent audit report have been fixed and covered with adversarial regression tests. The codebase is in a clean, production-quality state ready for Milestone M1.5 (Streaming Transport & Desktop Chat UI).
