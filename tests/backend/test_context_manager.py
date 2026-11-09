"""Tests for ContextManager token budgeting, non-truncation rules, and sliding window pruning."""

import pytest

from app.ai.context import ContextManager
from app.ai.exceptions import ContextWindowExceededError
from app.ai.types import MessageRole
from app.models.conversation import Message


def _make_msg(seq: int, role: str, content: str, status: str = "completed") -> Message:
    return Message(
        id=f"msg-{seq}",
        conversation_id="conv-test",
        sequence=seq,
        role=role,
        content=content,
        status=status,
    )


def test_token_estimation_conservative_heuristic():
    """Verify conservative token estimation adds framing overhead and divides by 3.5."""
    # Empty string should return framing overhead (4 tokens)
    assert ContextManager.estimate_tokens("") == 4

    # 35 characters -> ceil(35 / 3.5) + 4 = 10 + 4 = 14 tokens
    text_35 = "a" * 35
    assert ContextManager.estimate_tokens(text_35) == 14


def test_compute_budgets_invariants():
    """Verify budget partition satisfies: prompt_budget + max_output + margin == allocated_context."""
    allocated = 4096
    budget, max_out = ContextManager.compute_budgets(
        allocated_context=allocated,
        requested_max_output_tokens=512,
    )
    assert max_out == 512
    assert budget + max_out + ContextManager.FRAMING_MARGIN_TOKENS == allocated

    # Default output headroom clamping (quarter of context)
    allocated_small = 1024
    budget_small, max_out_small = ContextManager.compute_budgets(allocated_context=allocated_small)
    assert max_out_small == 256  # 1024 // 4
    assert budget_small + max_out_small + ContextManager.FRAMING_MARGIN_TOKENS == allocated_small


def test_user_prompt_exceeding_budget_is_rejected_never_truncated():
    """Verify latest user prompt exceeding input budget raises ContextWindowExceededError without truncation."""
    allocated = 512
    # Budget for 512: quarter is 128 max_output, framing 32 -> prompt budget = 352
    # Create prompt requiring > 400 tokens (~1500 chars)
    huge_prompt = "Never truncate user input! " * 60

    history = [_make_msg(1, "user", "hi"), _make_msg(2, "assistant", "hello")]

    with pytest.raises(ContextWindowExceededError) as exc_info:
        ContextManager.assemble_context(
            history=history,
            latest_user_content=huge_prompt,
            allocated_context=allocated,
        )

    # Error message must explain budget exceeded without truncating
    assert "exceeding available input budget" in str(exc_info.value)
    assert exc_info.value.code == "CONTEXT_WINDOW_EXCEEDED"


def test_system_prompt_exceeding_budget_is_rejected():
    """Verify system prompt exceeding budget is rejected."""
    allocated = 512
    huge_system = "System instruction " * 100

    with pytest.raises(ContextWindowExceededError) as exc_info:
        ContextManager.assemble_context(
            history=[],
            latest_user_content="short user message",
            allocated_context=allocated,
            system_prompt=huge_system,
        )

    assert "System prompt requires" in str(exc_info.value)


def test_sliding_window_prunes_oldest_turns_preserving_anchors():
    """Verify sliding window prunes oldest historical turns while preserving system and latest user prompt."""
    allocated = 512
    prompt_budget, max_out = ContextManager.compute_budgets(allocated)
    # prompt_budget is 352 tokens.

    system_text = "You are a helpful assistant."  # ~12 tokens
    latest_user = "What is the capital of France?"  # ~13 tokens

    # Create 10 historical turns (each ~30 tokens)
    history = []
    for i in range(1, 11):
        role = "user" if i % 2 == 1 else "assistant"
        history.append(_make_msg(i, role, f"Historical message number {i} with extra text words."))

    assembled, out_headroom, est_prompt = ContextManager.assemble_context(
        history=history,
        latest_user_content=latest_user,
        allocated_context=allocated,
        system_prompt=system_text,
    )

    # 1. First message must be the anchored system message
    assert assembled[0].role == MessageRole.SYSTEM
    assert assembled[0].content == system_text

    # 2. Last message must be the anchored latest user message (byte-for-byte unchanged)
    assert assembled[-1].role == MessageRole.USER
    assert assembled[-1].content == latest_user

    # 3. Intermediary turns must be in chronological order
    middle_turns = assembled[1:-1]
    assert len(middle_turns) > 0
    # Turns must have been pruned from the oldest turns (early turns pruned)
    # The newest historical turns must be present
    assert middle_turns[-1].content == history[-1].content


def test_failed_and_interrupted_turns_excluded_from_context():
    """Verify that messages with status != 'completed' are excluded from context assembly."""
    history = [
        _make_msg(1, "user", "Completed prompt 1", status="completed"),
        _make_msg(2, "assistant", "Completed response 1", status="completed"),
        _make_msg(3, "user", "Unfinished question", status="completed"),
        _make_msg(4, "assistant", "Interrupted response...", status="interrupted"),
        _make_msg(5, "assistant", "Failed response error", status="failed"),
        _make_msg(6, "user", "Follow-up question", status="completed"),
        _make_msg(7, "assistant", "Successful follow-up answer", status="completed"),
    ]

    assembled, _, _ = ContextManager.assemble_context(
        history=history,
        latest_user_content="Final question",
        allocated_context=2048,
    )

    contents = [m.content for m in assembled]
    assert "Completed prompt 1" in contents
    assert "Completed response 1" in contents
    # Unfinished question had an interrupted/failed assistant, so it cannot form a completed turn
    assert "Unfinished question" not in contents
    assert "Interrupted response..." not in contents
    assert "Failed response error" not in contents
    assert "Follow-up question" in contents
    assert "Successful follow-up answer" in contents
    assert "Final question" in contents


def test_turn_pair_atomicity_and_budget_bounds():
    """Verify turn pairs are admitted atomically; assistant is never admitted without user prompt."""
    allocated_context = 256
    prompt_budget, max_output = ContextManager.compute_budgets(allocated_context)

    latest_user = "Latest user prompt."
    latest_user_cost = ContextManager.estimate_tokens(latest_user)
    remaining_for_hist = prompt_budget - latest_user_cost

    # Turn 1: Fits comfortably
    m1 = _make_msg(1, "user", "Turn 1 question", status="completed")
    m2 = _make_msg(2, "assistant", "Turn 1 answer", status="completed")

    # Turn 2: Assistant fits alone, but user is large so the pair exceeds remaining_for_hist
    m3_huge_user = "X" * (remaining_for_hist * 4)
    m3 = _make_msg(3, "user", m3_huge_user, status="completed")
    m4 = _make_msg(4, "assistant", "Short assistant reply", status="completed")

    history = [m1, m2, m3, m4]

    assembled, _, est_tokens = ContextManager.assemble_context(
        history=history,
        latest_user_content=latest_user,
        allocated_context=allocated_context,
    )

    # Turn 2 pair was rejected as a whole because user turn exceeded remaining budget
    # Neither m3 nor m4 should be present
    contents = [m.content for m in assembled]
    assert "Short assistant reply" not in contents, "Assistant turn must never be orphaned!"
    assert m3_huge_user not in contents

    # First turn should be system or user, never assistant
    assert assembled[0].role == MessageRole.USER
    assert assembled[-1].content == latest_user
    assert est_tokens <= prompt_budget


def test_large_latest_user_prompt_preserved_without_truncation():
    """Verify that a large user prompt within budget preserves byte-for-byte and evicts history."""
    allocated_context = 512
    prompt_budget, _ = ContextManager.compute_budgets(allocated_context)

    # Large user prompt that takes ~70% of the input budget
    large_prompt = "Large user question text. " * 12
    user_cost = ContextManager.estimate_tokens(large_prompt)
    assert user_cost < prompt_budget

    history = [
        _make_msg(1, "user", "Old question 1", status="completed"),
        _make_msg(2, "assistant", "Old answer 1", status="completed"),
        _make_msg(3, "user", "Old question 2", status="completed"),
        _make_msg(4, "assistant", "Old answer 2", status="completed"),
    ]

    assembled, _, est_tokens = ContextManager.assemble_context(
        history=history,
        latest_user_content=large_prompt,
        allocated_context=allocated_context,
    )

    # Latest user prompt must be byte-for-byte identical (trimmed)
    assert assembled[-1].content == large_prompt.strip()
    assert assembled[-1].role == MessageRole.USER
    assert est_tokens <= prompt_budget
    # Messages in assembled must strictly alternate role (or start with system)
    for i in range(len(assembled) - 1):
        assert assembled[i].role != assembled[i + 1].role
