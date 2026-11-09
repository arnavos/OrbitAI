"""Provider-independent context management, token budgeting, and sliding window pruning."""

import math

from app.ai.exceptions import ContextWindowExceededError
from app.ai.types import ChatMessage, MessageRole
from app.models.conversation import Message


class ContextManager:
    """Manages context assembly, output headroom reservation, and deterministic history pruning."""

    DEFAULT_OUTPUT_HEADROOM = 1024
    MIN_OUTPUT_HEADROOM = 64
    FRAMING_MARGIN_TOKENS = 32
    CHARS_PER_TOKEN_ESTIMATE = 3.5
    PER_MESSAGE_FRAMING_OVERHEAD = 4

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        """Estimate token count for text using a conservative character-based heuristic.

        Uses 3.5 characters per token with a 4-token per-turn ChatML framing overhead.
        This conservatively overestimates English and code tokens to prevent context overflow.
        """
        if not text:
            return cls.PER_MESSAGE_FRAMING_OVERHEAD
        return (
            math.ceil(len(text) / cls.CHARS_PER_TOKEN_ESTIMATE) + cls.PER_MESSAGE_FRAMING_OVERHEAD
        )

    @classmethod
    def compute_budgets(
        cls,
        allocated_context: int,
        requested_max_output_tokens: int | None = None,
    ) -> tuple[int, int]:
        """Compute authoritative input prompt budget and reserved output headroom.

        Invariant:
            input_prompt_budget + max_output_tokens + framing_margin == allocated_context

        Returns:
            tuple[input_prompt_budget, max_output_tokens]
        """
        if allocated_context <= 0:
            raise ValueError("allocated_context must be a positive integer")

        # Reserve output headroom: min(requested or 1024, 25% of allocated context, 2048)
        quarter_alloc = max(cls.MIN_OUTPUT_HEADROOM, allocated_context // 4)
        if requested_max_output_tokens is not None and requested_max_output_tokens > 0:
            max_output = min(requested_max_output_tokens, quarter_alloc, 2048)
        else:
            max_output = min(cls.DEFAULT_OUTPUT_HEADROOM, quarter_alloc)

        # Enforce minimum output headroom
        max_output = max(cls.MIN_OUTPUT_HEADROOM, max_output)

        prompt_budget = allocated_context - max_output - cls.FRAMING_MARGIN_TOKENS
        if prompt_budget <= 0:
            raise ContextWindowExceededError(
                f"Allocated context ({allocated_context}) is too small to provide required "
                f"output headroom ({max_output}) and framing margin ({cls.FRAMING_MARGIN_TOKENS})"
            )

        return prompt_budget, max_output

    @classmethod
    def assemble_context(
        cls,
        history: list[Message],
        latest_user_content: str,
        allocated_context: int,
        system_prompt: str | None = None,
        requested_max_output_tokens: int | None = None,
    ) -> tuple[list[ChatMessage], int, int]:
        """Assemble chronological ChatMessage sequence strictly within input budget.

        Rules:
        1. System prompt (if any) is anchored and never pruned.
        2. Latest user prompt is anchored and NEVER truncated.
        3. If system prompt + latest user prompt > input_prompt_budget, request is rejected.
        4. Prior historical turns are added newest-first from completed turns only.
        5. Historical traversal halts upon first non-fitting turn to preserve unbroken continuity.

        Returns:
            tuple[assembled_messages, max_output_tokens, estimated_prompt_tokens]

        Raises:
            ContextWindowExceededError: If the mandatory prompt cannot fit within the budget.
        """
        prompt_budget, max_output = cls.compute_budgets(
            allocated_context=allocated_context,
            requested_max_output_tokens=requested_max_output_tokens,
        )

        budget_remaining = prompt_budget
        system_msg: ChatMessage | None = None

        # 1. Evaluate Anchor: System Prompt
        if system_prompt and system_prompt.strip():
            cleaned_sys = system_prompt.strip()
            sys_cost = cls.estimate_tokens(cleaned_sys)
            if sys_cost >= budget_remaining:
                raise ContextWindowExceededError(
                    f"System prompt requires ~{sys_cost} tokens, exceeding maximum input budget "
                    f"of {prompt_budget} tokens (allocated context: {allocated_context}, "
                    f"output reservation: {max_output})"
                )
            system_msg = ChatMessage(role=MessageRole.SYSTEM, content=cleaned_sys)
            budget_remaining -= sys_cost

        # 2. Evaluate Anchor: Latest User Message (NO SILENT TRUNCATION!)
        cleaned_user = latest_user_content.strip()
        if not cleaned_user:
            raise ValueError("User message content cannot be empty or purely whitespace")

        user_cost = cls.estimate_tokens(cleaned_user)
        if user_cost > budget_remaining:
            raise ContextWindowExceededError(
                f"User prompt requires ~{user_cost} tokens, exceeding available input budget "
                f"of {budget_remaining} tokens (total input budget: {prompt_budget}, "
                f"allocated context: {allocated_context}, output reservation: {max_output}). "
                "User message was safely persisted but inference cannot proceed."
            )

        latest_user_msg = ChatMessage(role=MessageRole.USER, content=cleaned_user)
        budget_remaining -= user_cost

        # 3. Fill Remaining Budget from History (Reverse Chronological Traversal)
        # Filter: Only completed turns (exclude failed, interrupted, or pending turns)
        valid_history = [
            m for m in history if m.status == "completed" and m.role in ("user", "assistant")
        ]

        admitted_history: list[ChatMessage] = []
        for turn in reversed(valid_history):
            turn_cost = cls.estimate_tokens(turn.content)
            if turn_cost <= budget_remaining:
                admitted_history.append(
                    ChatMessage(
                        role=MessageRole(turn.role),
                        content=turn.content,
                    )
                )
                budget_remaining -= turn_cost
            else:
                # Halt traversal: preserving unbroken contiguous conversation history is preferred
                # over omitting arbitrary intermediate turns
                break

        # Reverse admitted historical turns back to chronological order (oldest to newest)
        admitted_history.reverse()

        # Combine: [system (if any)] + [historical turns] + [latest user message]
        final_messages: list[ChatMessage] = []
        if system_msg:
            final_messages.append(system_msg)
        final_messages.extend(admitted_history)
        final_messages.append(latest_user_msg)

        total_estimated_prompt = prompt_budget - budget_remaining
        return final_messages, max_output, total_estimated_prompt
