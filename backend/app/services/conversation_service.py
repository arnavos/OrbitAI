"""Service layer managing conversation lifecycle, sequence persistence, and context."""

import asyncio
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context import ContextManager
from app.ai.exceptions import (
    AIError,
    ConversationNotFoundError,
)
from app.ai.registry import ModelRegistry
from app.ai.resources.manager import ResourceManager
from app.ai.resources.types import AdmissionOutcome
from app.ai.types import (
    FinishReason,
    GenerationConfig,
    InferenceRequest,
    TokenUsage,
)
from app.core.logging import logger
from app.db.base import utc_now
from app.models.conversation import Conversation, Message
from app.schemas.conversation import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
    MessageResponse,
    SendMessageRequest,
    SendMessageResponse,
)


class ConversationService:
    """Manages conversation thread state, atomic sequencing, and model inference orchestration.

    Deployment Invariant:
        The per-conversation in-memory lock (_conversation_locks) relies on a single-backend-worker
        process architecture. In multi-worker deployments, a distributed synchronization primitive
        (e.g., Redis or file locks) would be required to serialize turns per conversation.
        However, the database-level atomic sequence allocation (UPDATE ... RETURNING) and
        compound unique constraint ensure database integrity across any number of processes.
    """

    _conversation_locks: dict[str, asyncio.Lock] = {}
    _locks_guard = asyncio.Lock()

    @classmethod
    @asynccontextmanager
    async def _acquire_conversation_lock(cls, conversation_id: str) -> AsyncGenerator[None]:
        """Serialize simultaneous requests targeting the same conversation thread."""
        async with cls._locks_guard:
            lock = cls._conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            try:
                yield
            finally:
                # Cleanup idle locks if not held
                async with cls._locks_guard:
                    if not lock.locked() and conversation_id in cls._conversation_locks:
                        cls._conversation_locks.pop(conversation_id, None)

    # --------------------------------------------------------------------------
    # Conversation CRUD Operations
    # --------------------------------------------------------------------------

    @classmethod
    async def create_conversation(
        cls,
        session: AsyncSession,
        payload: ConversationCreate,
    ) -> ConversationResponse:
        """Create and persist a new conversation thread."""
        conv_id = str(uuid.uuid4())
        title = (
            payload.title.strip() if payload.title and payload.title.strip() else "New Conversation"
        )

        conv = Conversation(
            id=conv_id,
            title=title,
            model_id=payload.model_id,
            last_sequence=0,
        )
        session.add(conv)
        await session.commit()
        await session.refresh(conv)

        return ConversationResponse(
            id=conv.id,
            title=conv.title,
            model_id=conv.model_id,
            last_sequence=conv.last_sequence,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            message_count=0,
        )

    @classmethod
    async def get_conversation(
        cls,
        session: AsyncSession,
        conversation_id: str,
    ) -> ConversationResponse:
        """Retrieve conversation metadata and message count by ID."""
        conv = await session.get(Conversation, conversation_id)
        if not conv:
            raise ConversationNotFoundError(f"Conversation '{conversation_id}' not found")

        count_stmt = select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id
        )
        count_res = await session.execute(count_stmt)
        msg_count = count_res.scalar_one()

        return ConversationResponse(
            id=conv.id,
            title=conv.title,
            model_id=conv.model_id,
            last_sequence=conv.last_sequence,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            message_count=msg_count,
        )

    @classmethod
    async def list_conversations(
        cls,
        session: AsyncSession,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ConversationResponse], int]:
        """List conversation threads ordered by updated_at DESC."""
        total_stmt = select(func.count(Conversation.id))
        total_res = await session.execute(total_stmt)
        total = total_res.scalar_one()

        # Query conversations with message count via left join
        stmt = (
            select(
                Conversation,
                func.count(Message.id).label("msg_count"),
            )
            .outerjoin(Message, Conversation.id == Message.conversation_id)
            .group_by(Conversation.id)
            .order_by(Conversation.updated_at.desc())
            .offset(offset)
            .limit(limit)
        )
        res = await session.execute(stmt)
        rows = res.all()

        items = [
            ConversationResponse(
                id=conv.id,
                title=conv.title,
                model_id=conv.model_id,
                last_sequence=conv.last_sequence,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                message_count=cnt,
            )
            for conv, cnt in rows
        ]
        return items, total

    @classmethod
    async def update_conversation(
        cls,
        session: AsyncSession,
        conversation_id: str,
        payload: ConversationUpdate,
    ) -> ConversationResponse:
        """Update conversation title or default model."""
        async with cls._acquire_conversation_lock(conversation_id):
            conv = await session.get(Conversation, conversation_id)
            if not conv:
                raise ConversationNotFoundError(f"Conversation '{conversation_id}' not found")

            if payload.title is not None:
                cleaned = payload.title.strip()
                if cleaned:
                    conv.title = cleaned
            if payload.model_id is not None:
                conv.model_id = payload.model_id.strip() or None

            conv.updated_at = utc_now()
            await session.commit()
            await session.refresh(conv)

            return await cls.get_conversation(session, conversation_id)

    @classmethod
    async def delete_conversation(
        cls,
        session: AsyncSession,
        conversation_id: str,
    ) -> bool:
        """Cascade delete a conversation thread and all contained turns."""
        async with cls._acquire_conversation_lock(conversation_id):
            conv = await session.get(Conversation, conversation_id)
            if not conv:
                raise ConversationNotFoundError(f"Conversation '{conversation_id}' not found")

            await session.delete(conv)
            await session.commit()
            return True

    @classmethod
    async def list_messages(
        cls,
        session: AsyncSession,
        conversation_id: str,
        limit: int = 100,
        before_sequence: int | None = None,
    ) -> tuple[list[MessageResponse], int]:
        """Retrieve ordered message history for a conversation thread."""
        conv = await session.get(Conversation, conversation_id)
        if not conv:
            raise ConversationNotFoundError(f"Conversation '{conversation_id}' not found")

        total_stmt = select(func.count(Message.id)).where(
            Message.conversation_id == conversation_id
        )
        total_res = await session.execute(total_stmt)
        total = total_res.scalar_one()

        stmt = select(Message).where(Message.conversation_id == conversation_id)
        if before_sequence is not None:
            stmt = stmt.where(Message.sequence < before_sequence)
        stmt = stmt.order_by(Message.sequence.asc()).limit(limit)

        res = await session.execute(stmt)
        messages = res.scalars().all()

        return [MessageResponse.model_validate(m) for m in messages], total

    # --------------------------------------------------------------------------
    # Synchronous Message Submission & Inference Orchestration
    # --------------------------------------------------------------------------

    @classmethod
    async def send_message(
        cls,
        session_factory,
        conversation_id: str,
        payload: SendMessageRequest,
        registry: ModelRegistry,
        resource_manager: ResourceManager | None = None,
    ) -> SendMessageResponse:
        """Atomically persist user message, assemble context, execute inference, and store response.

        Guarantees:
        1. User message is committed BEFORE model inference begins.
        2. Database write locks are NEVER held across model inference.
        3. Simultaneous requests to the same thread are serialized in-memory.
        4. Sequence numbers are allocated atomically via UPDATE ... RETURNING.
        5. Cancellation preserves user message, marks assistant as interrupted, and re-raises.
        """
        async with cls._acquire_conversation_lock(conversation_id):
            # ------------------------------------------------------------------
            # Phase 1: Atomically Allocate Sequence & Persist User Turn
            # ------------------------------------------------------------------
            user_msg_id = str(uuid.uuid4())
            user_content = payload.content.strip()
            if not user_content:
                raise ValueError("User message content cannot be empty")

            now = utc_now()
            async with session_factory() as session:
                seq_stmt = (
                    update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(
                        last_sequence=Conversation.last_sequence + 1,
                        updated_at=now,
                    )
                    .returning(
                        Conversation.last_sequence, Conversation.model_id, Conversation.title
                    )
                )
                seq_res = await session.execute(seq_stmt)
                row = seq_res.first()
                if not row:
                    raise ConversationNotFoundError(f"Conversation '{conversation_id}' not found")

                user_seq, conv_model_id, conv_title = row

                user_msg = Message(
                    id=user_msg_id,
                    conversation_id=conversation_id,
                    sequence=user_seq,
                    role="user",
                    content=user_content,
                    status="completed",
                    created_at=now,
                    updated_at=now,
                )
                session.add(user_msg)

                # Auto-generate title from first user prompt if still default
                if conv_title == "New Conversation" and user_seq == 1:
                    snippet = user_content[:40].strip()
                    if len(user_content) > 40:
                        snippet += "..."
                    await session.execute(
                        update(Conversation)
                        .where(Conversation.id == conversation_id)
                        .values(title=snippet)
                    )

                await session.commit()
                # Snapshot user response dto
                user_dto = MessageResponse.model_validate(user_msg)

            # ------------------------------------------------------------------
            # Phase 2: Context Assembly & Headroom Determination (No DB Locks)
            # ------------------------------------------------------------------
            target_model = payload.model_id or conv_model_id or "ollama/llama3.2:3b"

            # Query historical completed turns for context construction
            async with session_factory() as session:
                hist_stmt = (
                    select(Message)
                    .where(
                        Message.conversation_id == conversation_id,
                        Message.status == "completed",
                        Message.id != user_msg_id,  # Exclude current prompt from history
                    )
                    .order_by(Message.sequence.asc())
                )
                hist_res = await session.execute(hist_stmt)
                history = list(hist_res.scalars().all())

            # Determine allocated context limit from ResourceManager if attached
            allocated_context = 4096  # Standard baseline fallback
            if resource_manager is not None:
                try:
                    eval_res = await resource_manager.evaluate_admission(
                        model_id=target_model,
                        requested_context=4096,
                    )
                    if eval_res.outcome == AdmissionOutcome.ALLOW_WITH_REDUCED_CONTEXT:
                        allocated_context = eval_res.allocated_context
                    elif eval_res.outcome == AdmissionOutcome.ALLOW:
                        allocated_context = eval_res.allocated_context or 4096
                except Exception as eval_err:
                    logger.warning(
                        f"Error consulting ResourceManager: {eval_err}, using default context"
                    )

            # Assemble context and calculate maximum output headroom
            assembled_messages, max_output, est_prompt_tokens = ContextManager.assemble_context(
                history=history,
                latest_user_content=user_content,
                allocated_context=allocated_context,
                system_prompt=payload.system_prompt,
                requested_max_output_tokens=payload.max_output_tokens,
            )

            # ------------------------------------------------------------------
            # Phase 3: Allocate Sequence & Commit Assistant Placeholder
            # ------------------------------------------------------------------
            asst_msg_id = str(uuid.uuid4())
            asst_seq = 0
            async with session_factory() as session:
                asst_seq_stmt = (
                    update(Conversation)
                    .where(Conversation.id == conversation_id)
                    .values(
                        last_sequence=Conversation.last_sequence + 1,
                        updated_at=utc_now(),
                    )
                    .returning(Conversation.last_sequence)
                )
                asst_seq_res = await session.execute(asst_seq_stmt)
                asst_seq = asst_seq_res.scalar_one()

                asst_msg = Message(
                    id=asst_msg_id,
                    conversation_id=conversation_id,
                    sequence=asst_seq,
                    role="assistant",
                    content="",
                    status="streaming",
                    model_id=target_model,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                session.add(asst_msg)
                await session.commit()

            # ------------------------------------------------------------------
            # Phase 4: Synchronous Model Inference (ZERO Database Locks Held)
            # ------------------------------------------------------------------
            provider = await registry.resolve_provider_for_model(target_model)
            model_desc = await registry.get_model(target_model)

            gen_config = GenerationConfig(
                temperature=payload.temperature if payload.temperature is not None else 0.7,
                max_output_tokens=max_output,
                extra_params={"num_ctx": allocated_context},
            )

            inf_req = InferenceRequest(
                request_id=asst_msg_id,
                model_id=model_desc.id,
                messages=assembled_messages,
                config=gen_config,
            )

            partial_tokens: list[str] = []
            final_status = "completed"
            final_finish_reason: str | None = None
            final_usage: TokenUsage | None = None
            final_error: str | None = None

            try:
                async for delta in provider.stream_inference(inf_req):
                    if delta.text:
                        partial_tokens.append(delta.text)
                    if delta.finish_reason is not None:
                        final_finish_reason = (
                            delta.finish_reason.value
                            if hasattr(delta.finish_reason, "value")
                            else str(delta.finish_reason)
                        )
                    if delta.usage is not None:
                        final_usage = delta.usage
                    if delta.error_detail is not None:
                        final_error = delta.error_detail.message
                        final_status = "failed"

                full_content = "".join(partial_tokens)
                if final_status != "failed":
                    if final_finish_reason in (FinishReason.STOP.value, FinishReason.LENGTH.value):
                        final_status = "completed"
                    elif final_finish_reason == FinishReason.CANCELLED.value:
                        final_status = "interrupted"
                    else:
                        final_status = "completed"
                        final_finish_reason = FinishReason.STOP.value

            except asyncio.CancelledError:
                # Caller cancelled execution during streaming
                logger.warning(f"Inference for message '{asst_msg_id}' cancelled by caller")
                full_content = "".join(partial_tokens)
                final_status = "interrupted"
                final_finish_reason = FinishReason.CANCELLED.value
                final_error = "Inference cancelled by caller"

                # Shield final database update so cancellation does not abandon unpersisted state
                await asyncio.shield(
                    cls._finalize_assistant_turn(
                        session_factory=session_factory,
                        message_id=asst_msg_id,
                        content=full_content,
                        status=final_status,
                        finish_reason=final_finish_reason,
                        usage=final_usage,
                        error_message=final_error,
                    )
                )
                # Re-raise CancelledError to preserve asynchronous cancellation protocol
                raise

            except Exception as inf_exc:
                logger.error(f"Inference exception during message '{asst_msg_id}': {inf_exc}")
                full_content = "".join(partial_tokens)
                final_status = "failed"
                final_finish_reason = FinishReason.ERROR.value
                final_error = str(inf_exc)

            # ------------------------------------------------------------------
            # Phase 5: Finalize Assistant Message State
            # ------------------------------------------------------------------
            asst_dto = await cls._finalize_assistant_turn(
                session_factory=session_factory,
                message_id=asst_msg_id,
                content=full_content,
                status=final_status,
                finish_reason=final_finish_reason,
                usage=final_usage,
                error_message=final_error,
            )

            if final_status == "failed":
                raise AIError(
                    message=f"Model inference failed: {final_error or 'Unknown error'}",
                    code="INFERENCE_FAILED",
                )

            return SendMessageResponse(
                user_message=user_dto,
                assistant_message=asst_dto,
            )

    @classmethod
    async def _finalize_assistant_turn(
        cls,
        session_factory,
        message_id: str,
        content: str,
        status: str,
        finish_reason: str | None,
        usage: TokenUsage | None,
        error_message: str | None,
    ) -> MessageResponse:
        """Update and commit terminal state of assistant turn."""
        prompt_toks = usage.prompt_tokens if usage else None
        comp_toks = usage.completion_tokens if usage else None

        now = utc_now()
        async with session_factory() as session:
            stmt = (
                update(Message)
                .where(Message.id == message_id)
                .values(
                    content=content,
                    status=status,
                    finish_reason=finish_reason,
                    prompt_tokens=prompt_toks,
                    completion_tokens=comp_toks,
                    error_message=error_message,
                    updated_at=now,
                )
                .returning(Message)
            )
            res = await session.execute(stmt)
            updated_msg = res.scalar_one()
            await session.commit()
            return MessageResponse.model_validate(updated_msg)

    # --------------------------------------------------------------------------
    # Crash Recovery Reconciliation
    # --------------------------------------------------------------------------

    @classmethod
    async def reconcile_crashed_generations(cls, session: AsyncSession) -> int:
        """Startup hook reconciling in-flight messages interrupted by an unexpected crash."""
        stmt = (
            update(Message)
            .where(Message.status.in_(["streaming", "pending"]))
            .values(
                status="interrupted",
                finish_reason="aborted",
                error_message="Process terminated unexpectedly during generation",
                updated_at=utc_now(),
            )
        )
        res = await session.execute(stmt)
        await session.commit()
        count = res.rowcount
        if count > 0:
            logger.warning(
                f"Crash recovery reconciliation: marked {count} "
                "dangling in-flight message(s) as interrupted"
            )
        return count
