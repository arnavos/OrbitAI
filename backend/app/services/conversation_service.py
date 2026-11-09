"""Service layer managing conversation lifecycle, sequence persistence, and context."""

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.context import ContextManager
from app.ai.exceptions import (
    AIError,
    ConversationNotFoundError,
)
from app.ai.provider import ModelProvider
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
from app.schemas.streaming import (
    STREAM_PROTOCOL_VERSION,
    MessageDeltaPayload,
    StreamCancelledPayload,
    StreamCompletedPayload,
    StreamEventEnvelope,
    StreamEventType,
    StreamFailedPayload,
    StreamInterruptedPayload,
    StreamStartedPayload,
    StreamUserMessageSnapshot,
    format_ping,
)


class _LockEntry:
    """Reference-counted lock descriptor for per-conversation serialization."""

    __slots__ = ("lock", "ref_count")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.ref_count = 0


class _ActiveGeneration:
    """Tracks an in-flight generation for cancellation and status monitoring."""

    __slots__ = (
        "conversation_id",
        "message_id",
        "model_id",
        "cancel_event",
        "is_explicit_cancel",
        "provider",
        "task",
    )

    def __init__(
        self,
        conversation_id: str,
        message_id: str,
        model_id: str,
        cancel_event: asyncio.Event,
        provider: ModelProvider | None = None,
    ) -> None:
        self.conversation_id = conversation_id
        self.message_id = message_id
        self.model_id = model_id
        self.cancel_event = cancel_event
        self.is_explicit_cancel = False
        self.provider = provider
        self.task: asyncio.Task | None = None


class _PreparedTurn:
    """Internal container for prepared turn parameters prior to inference."""

    __slots__ = (
        "user_dto",
        "user_msg_id",
        "user_seq",
        "user_content",
        "asst_msg_id",
        "asst_seq",
        "target_model",
        "allocated_context",
        "assembled_messages",
        "max_output",
        "gen_config",
        "created_at",
    )

    def __init__(
        self,
        user_dto: MessageResponse,
        user_msg_id: str,
        user_seq: int,
        user_content: str,
        asst_msg_id: str,
        asst_seq: int,
        target_model: str,
        allocated_context: int,
        assembled_messages: list,
        max_output: int,
        gen_config: GenerationConfig,
        created_at: datetime,
    ) -> None:
        self.user_dto = user_dto
        self.user_msg_id = user_msg_id
        self.user_seq = user_seq
        self.user_content = user_content
        self.asst_msg_id = asst_msg_id
        self.asst_seq = asst_seq
        self.target_model = target_model
        self.allocated_context = allocated_context
        self.assembled_messages = assembled_messages
        self.max_output = max_output
        self.gen_config = gen_config
        self.created_at = created_at


class ConversationService:
    """Manages conversation thread state, atomic sequencing, and model inference orchestration.

    Deployment Invariant:
        The per-conversation in-memory lock (_lock_entries) relies on a single-backend-worker
        process architecture. In multi-worker deployments, a distributed synchronization primitive
        (e.g., Redis or file locks) would be required to serialize turns per conversation.
        However, the database-level atomic sequence allocation (UPDATE ... RETURNING) and
        compound unique constraint ensure database integrity across any number of processes.
    """

    _lock_entries: dict[str, _LockEntry] = {}
    _locks_guard = asyncio.Lock()

    _active_generations: dict[str, _ActiveGeneration] = {}
    _active_generations_guard = asyncio.Lock()

    @classmethod
    @asynccontextmanager
    async def _acquire_conversation_lock(cls, conversation_id: str) -> AsyncGenerator[None]:
        """Serialize simultaneous requests targeting the same thread with ref-counted cleanup."""
        # 1. Register intent and increment ref_count under global guard
        async with cls._locks_guard:
            if conversation_id not in cls._lock_entries:
                cls._lock_entries[conversation_id] = _LockEntry()
            entry = cls._lock_entries[conversation_id]
            entry.ref_count += 1

        lock_acquired = False
        try:
            # 2. Await conversation lock (NEVER hold _locks_guard while awaiting)
            await entry.lock.acquire()
            lock_acquired = True
            yield
        finally:
            # 3. If lock was acquired, release it
            if lock_acquired:
                entry.lock.release()

            # 4. Decrement reference count and cleanup idle entry (shielded against cancellation)
            async def _cleanup() -> None:
                async with cls._locks_guard:
                    entry.ref_count -= 1
                    if entry.ref_count == 0 and cls._lock_entries.get(conversation_id) is entry:
                        cls._lock_entries.pop(conversation_id, None)

            await asyncio.shield(_cleanup())

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
    # Turn Preparation & Finalization Internal Helpers
    # --------------------------------------------------------------------------

    @classmethod
    async def _prepare_turn(
        cls,
        session_factory,
        conversation_id: str,
        payload: SendMessageRequest,
        resource_manager: ResourceManager | None = None,
    ) -> _PreparedTurn:
        """Atomically persist user message, assemble context, and commit assistant placeholder."""
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
                .returning(Conversation.last_sequence, Conversation.model_id, Conversation.title)
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
            user_dto = MessageResponse.model_validate(user_msg)

        # Phase 2: Context Assembly & Headroom Determination
        target_model = payload.model_id or conv_model_id or "ollama/llama3.2:3b"

        async with session_factory() as session:
            hist_stmt = (
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.status == "completed",
                    Message.id != user_msg_id,
                )
                .order_by(Message.sequence.asc())
            )
            hist_res = await session.execute(hist_stmt)
            history = list(hist_res.scalars().all())

        allocated_context = 4096
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

        assembled_messages, max_output, _ = ContextManager.assemble_context(
            history=history,
            latest_user_content=user_content,
            allocated_context=allocated_context,
            system_prompt=payload.system_prompt,
            requested_max_output_tokens=payload.max_output_tokens,
        )

        # Phase 3: Allocate Sequence & Commit Assistant Placeholder
        asst_msg_id = str(uuid.uuid4())
        asst_seq = 0
        asst_created_at = utc_now()
        async with session_factory() as session:
            asst_seq_stmt = (
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(
                    last_sequence=Conversation.last_sequence + 1,
                    updated_at=asst_created_at,
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
                created_at=asst_created_at,
                updated_at=asst_created_at,
            )
            session.add(asst_msg)
            await session.commit()

        gen_config = GenerationConfig(
            temperature=payload.temperature if payload.temperature is not None else 0.7,
            max_output_tokens=max_output,
            extra_params={"num_ctx": allocated_context},
        )

        return _PreparedTurn(
            user_dto=user_dto,
            user_msg_id=user_msg_id,
            user_seq=user_seq,
            user_content=user_content,
            asst_msg_id=asst_msg_id,
            asst_seq=asst_seq,
            target_model=target_model,
            allocated_context=allocated_context,
            assembled_messages=assembled_messages,
            max_output=max_output,
            gen_config=gen_config,
            created_at=asst_created_at,
        )

    # --------------------------------------------------------------------------
    # Explicit Turn Cancellation
    # --------------------------------------------------------------------------

    @classmethod
    async def cancel_turn(
        cls,
        session_factory,
        conversation_id: str,
    ) -> tuple[bool, str | None, str]:
        """Explicitly cancel active inference turn for a conversation thread.

        Returns:
            (cancelled: bool, message_id: str | None, detail: str)
        """
        async with session_factory() as session:
            conv = await session.get(Conversation, conversation_id)
            if not conv:
                raise ConversationNotFoundError(f"Conversation '{conversation_id}' not found")

        active_gen: _ActiveGeneration | None = None
        async with cls._active_generations_guard:
            active_gen = cls._active_generations.get(conversation_id)
            if active_gen is not None:
                active_gen.is_explicit_cancel = True
                active_gen.cancel_event.set()

        if active_gen is None:
            return False, None, "No active generation in progress for this conversation"

        msg_id = active_gen.message_id
        if active_gen.provider and hasattr(active_gen.provider, "cancel_inference"):
            try:
                await active_gen.provider.cancel_inference(msg_id)
            except Exception as e:
                logger.warning(f"Error calling cancel_inference on provider: {e}")

        if active_gen.task and not active_gen.task.done():
            active_gen.task.cancel()

        return True, msg_id, "Inference cancellation initiated"

    # --------------------------------------------------------------------------
    # Streaming Token Delivery (Server-Sent Events)
    # --------------------------------------------------------------------------

    @classmethod
    async def send_message_stream(
        cls,
        session_factory,
        conversation_id: str,
        payload: SendMessageRequest,
        registry: ModelRegistry,
        resource_manager: ResourceManager | None = None,
        disconnect_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[str]:
        """Atomically persist turns and stream token deltas as W3C SSE frames."""
        async with cls._acquire_conversation_lock(conversation_id):
            prepared = await cls._prepare_turn(
                session_factory=session_factory,
                conversation_id=conversation_id,
                payload=payload,
                resource_manager=resource_manager,
            )

            event_seq = 0
            has_emitted_terminal = False
            has_finalized_db = False

            # Emit stream.started
            start_payload = StreamStartedPayload(
                user_message=StreamUserMessageSnapshot(
                    id=prepared.user_dto.id,
                    conversation_id=prepared.user_dto.conversation_id,
                    sequence=prepared.user_dto.sequence,
                    role="user",
                    content=prepared.user_dto.content,
                    status=prepared.user_dto.status,
                    created_at=prepared.user_dto.created_at,
                ),
                assistant_message_id=prepared.asst_msg_id,
                assistant_sequence=prepared.asst_seq,
                model_id=prepared.target_model,
                allocated_context=prepared.allocated_context,
                created_at=prepared.created_at,
            )
            start_envelope = StreamEventEnvelope[StreamStartedPayload](
                protocol_version=STREAM_PROTOCOL_VERSION,
                event_type=StreamEventType.STREAM_STARTED,
                conversation_id=conversation_id,
                message_id=prepared.asst_msg_id,
                event_seq=event_seq,
                payload=start_payload,
            )
            yield start_envelope.format_sse()

            active_gen = _ActiveGeneration(
                conversation_id=conversation_id,
                message_id=prepared.asst_msg_id,
                model_id=prepared.target_model,
                cancel_event=asyncio.Event(),
            )
            async with cls._active_generations_guard:
                cls._active_generations[conversation_id] = active_gen

            partial_tokens: list[str] = []
            final_status = "completed"
            final_finish_reason: str | None = None
            final_usage: TokenUsage | None = None
            final_error: str | None = None
            token_index = 0
            start_time = time.perf_counter()

            # Background disconnect monitor
            disc_task: asyncio.Task | None = None
            if disconnect_check is not None:

                async def _monitor_disconnect() -> None:
                    try:
                        while not active_gen.cancel_event.is_set():
                            if await disconnect_check():
                                active_gen.cancel_event.set()
                                if active_gen.provider and hasattr(
                                    active_gen.provider, "cancel_inference"
                                ):
                                    await active_gen.provider.cancel_inference(prepared.asst_msg_id)
                                break
                            await asyncio.sleep(0.05)
                    except asyncio.CancelledError:
                        pass

                disc_task = asyncio.create_task(_monitor_disconnect())

            try:
                provider = await registry.resolve_provider_for_model(prepared.target_model)
                active_gen.provider = provider
                model_desc = await registry.get_model(prepared.target_model)

                inf_req = InferenceRequest(
                    request_id=prepared.asst_msg_id,
                    model_id=model_desc.id,
                    messages=prepared.assembled_messages,
                    config=prepared.gen_config,
                )

                stream_iter = provider.stream_inference(inf_req)
                while True:
                    if active_gen.cancel_event.is_set():
                        final_status = "interrupted"
                        final_finish_reason = FinishReason.CANCELLED.value
                        final_error = "Inference cancelled"
                        break

                    try:
                        delta = await asyncio.wait_for(anext(stream_iter), timeout=15.0)
                    except TimeoutError:
                        yield format_ping()
                        continue
                    except StopAsyncIteration:
                        break

                    if delta.text:
                        partial_tokens.append(delta.text)
                        event_seq += 1
                        delta_payload = MessageDeltaPayload(
                            text=delta.text,
                            token_index=token_index,
                            accumulated_tokens=len(partial_tokens),
                        )
                        token_index += 1
                        delta_envelope = StreamEventEnvelope[MessageDeltaPayload](
                            protocol_version=STREAM_PROTOCOL_VERSION,
                            event_type=StreamEventType.MESSAGE_DELTA,
                            conversation_id=conversation_id,
                            message_id=prepared.asst_msg_id,
                            event_seq=event_seq,
                            payload=delta_payload,
                        )
                        yield delta_envelope.format_sse()

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
                        final_finish_reason = final_finish_reason or FinishReason.ERROR.value

                full_content = "".join(partial_tokens)

                # Validate final finish reason
                if final_status not in ("failed", "interrupted"):
                    if final_finish_reason in (FinishReason.STOP.value, FinishReason.LENGTH.value):
                        final_status = "completed"
                    elif final_finish_reason == FinishReason.CANCELLED.value:
                        final_status = "interrupted"
                    elif final_finish_reason is None:
                        final_status = "failed"
                        final_finish_reason = FinishReason.ERROR.value
                        final_error = (
                            "Inference stream terminated prematurely without terminal finish reason"
                        )
                    else:
                        final_status = "failed"
                        final_finish_reason = FinishReason.ERROR.value
                        final_error = (
                            "Inference stream terminated with unsupported finish reason: "
                            f"{final_finish_reason}"
                        )

                # 1. Normal execution: persist terminal turn to DB
                duration_ms = (time.perf_counter() - start_time) * 1000.0

                await cls._finalize_assistant_turn(
                    session_factory=session_factory,
                    message_id=prepared.asst_msg_id,
                    content=full_content,
                    status=final_status,
                    finish_reason=final_finish_reason,
                    usage=final_usage,
                    error_message=final_error,
                )
                has_finalized_db = True

                # 2. Emit terminal SSE event
                if not has_emitted_terminal:
                    has_emitted_terminal = True
                    event_seq += 1

                    if final_status == "completed":
                        completed_envelope = StreamEventEnvelope[StreamCompletedPayload](
                            protocol_version=STREAM_PROTOCOL_VERSION,
                            event_type=StreamEventType.STREAM_COMPLETED,
                            conversation_id=conversation_id,
                            message_id=prepared.asst_msg_id,
                            event_seq=event_seq,
                            payload=StreamCompletedPayload(
                                finish_reason=final_finish_reason or "stop",
                                prompt_tokens=final_usage.prompt_tokens if final_usage else None,
                                completion_tokens=final_usage.completion_tokens
                                if final_usage
                                else None,
                                total_tokens=final_usage.total_tokens if final_usage else None,
                                duration_ms=duration_ms,
                            ),
                        )
                        yield completed_envelope.format_sse()
                    elif final_status == "interrupted":
                        if (
                            active_gen.is_explicit_cancel
                            or final_finish_reason == FinishReason.CANCELLED.value
                        ):
                            cancelled_envelope = StreamEventEnvelope[StreamCancelledPayload](
                                protocol_version=STREAM_PROTOCOL_VERSION,
                                event_type=StreamEventType.STREAM_CANCELLED,
                                conversation_id=conversation_id,
                                message_id=prepared.asst_msg_id,
                                event_seq=event_seq,
                                payload=StreamCancelledPayload(
                                    reason="cancelled_by_user",
                                    partial_tokens_count=len(partial_tokens),
                                    partial_text_length=len(full_content),
                                ),
                            )
                            yield cancelled_envelope.format_sse()
                        else:
                            interrupted_envelope = StreamEventEnvelope[StreamInterruptedPayload](
                                protocol_version=STREAM_PROTOCOL_VERSION,
                                event_type=StreamEventType.STREAM_INTERRUPTED,
                                conversation_id=conversation_id,
                                message_id=prepared.asst_msg_id,
                                event_seq=event_seq,
                                payload=StreamInterruptedPayload(
                                    reason="transport_interrupted",
                                    partial_tokens_count=len(partial_tokens),
                                    partial_text_length=len(full_content),
                                ),
                            )
                            yield interrupted_envelope.format_sse()
                    else:  # final_status == "failed"
                        failed_envelope = StreamEventEnvelope[StreamFailedPayload](
                            protocol_version=STREAM_PROTOCOL_VERSION,
                            event_type=StreamEventType.STREAM_FAILED,
                            conversation_id=conversation_id,
                            message_id=prepared.asst_msg_id,
                            event_seq=event_seq,
                            payload=StreamFailedPayload(
                                error_code="INFERENCE_FAILED"
                                if final_finish_reason != FinishReason.ERROR.value
                                else "PROVIDER_ERROR",
                                error_message=final_error or "Inference error",
                                details=None,
                            ),
                        )
                        yield failed_envelope.format_sse()

            except asyncio.CancelledError:
                logger.warning(
                    f"Inference stream for '{prepared.asst_msg_id}' cancelled by task/caller"
                )
                full_content = "".join(partial_tokens)
                final_status = "interrupted"
                final_finish_reason = FinishReason.CANCELLED.value
                final_error = "Inference cancelled by caller"
                if active_gen.provider and hasattr(active_gen.provider, "cancel_inference"):
                    with suppress(Exception):
                        await active_gen.provider.cancel_inference(prepared.asst_msg_id)
                raise

            except Exception as inf_exc:
                logger.error(
                    f"Inference exception during stream for '{prepared.asst_msg_id}': {inf_exc}"
                )
                full_content = "".join(partial_tokens)
                final_status = "failed"
                final_finish_reason = FinishReason.ERROR.value
                final_error = str(inf_exc)

                # Persist failed turn to DB
                await cls._finalize_assistant_turn(
                    session_factory=session_factory,
                    message_id=prepared.asst_msg_id,
                    content=full_content,
                    status=final_status,
                    finish_reason=final_finish_reason,
                    usage=final_usage,
                    error_message=final_error,
                )
                has_finalized_db = True

                # Emit stream.failed
                if not has_emitted_terminal:
                    has_emitted_terminal = True
                    event_seq += 1
                    failed_envelope = StreamEventEnvelope[StreamFailedPayload](
                        protocol_version=STREAM_PROTOCOL_VERSION,
                        event_type=StreamEventType.STREAM_FAILED,
                        conversation_id=conversation_id,
                        message_id=prepared.asst_msg_id,
                        event_seq=event_seq,
                        payload=StreamFailedPayload(
                            error_code="PROVIDER_ERROR",
                            error_message=final_error,
                            details=None,
                        ),
                    )
                    yield failed_envelope.format_sse()

            finally:
                if disc_task and not disc_task.done():
                    disc_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await disc_task

                async with cls._active_generations_guard:
                    if cls._active_generations.get(conversation_id) is active_gen:
                        cls._active_generations.pop(conversation_id, None)

                # Shielded fallback persistence if CancelledError or unexpected exit occurred
                if not has_finalized_db:
                    full_content = "".join(partial_tokens)
                    await asyncio.shield(
                        cls._finalize_assistant_turn(
                            session_factory=session_factory,
                            message_id=prepared.asst_msg_id,
                            content=full_content,
                            status=final_status,
                            finish_reason=final_finish_reason,
                            usage=final_usage,
                            error_message=final_error,
                        )
                    )

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
            prepared = await cls._prepare_turn(
                session_factory=session_factory,
                conversation_id=conversation_id,
                payload=payload,
                resource_manager=resource_manager,
            )

            active_gen = _ActiveGeneration(
                conversation_id=conversation_id,
                message_id=prepared.asst_msg_id,
                model_id=prepared.target_model,
                cancel_event=asyncio.Event(),
            )
            async with cls._active_generations_guard:
                cls._active_generations[conversation_id] = active_gen

            partial_tokens: list[str] = []
            final_status = "completed"
            final_finish_reason: str | None = None
            final_usage: TokenUsage | None = None
            final_error: str | None = None

            try:
                provider = await registry.resolve_provider_for_model(prepared.target_model)
                active_gen.provider = provider
                model_desc = await registry.get_model(prepared.target_model)

                inf_req = InferenceRequest(
                    request_id=prepared.asst_msg_id,
                    model_id=model_desc.id,
                    messages=prepared.assembled_messages,
                    config=prepared.gen_config,
                )

                async for delta in provider.stream_inference(inf_req):
                    if active_gen.cancel_event.is_set():
                        final_status = "interrupted"
                        final_finish_reason = FinishReason.CANCELLED.value
                        final_error = "Inference cancelled"
                        break

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
                        final_finish_reason = final_finish_reason or FinishReason.ERROR.value

                full_content = "".join(partial_tokens)
                if final_status != "failed" and final_status != "interrupted":
                    if final_finish_reason in (FinishReason.STOP.value, FinishReason.LENGTH.value):
                        final_status = "completed"
                    elif final_finish_reason == FinishReason.CANCELLED.value:
                        final_status = "interrupted"
                    elif final_finish_reason is None:
                        final_status = "failed"
                        final_finish_reason = FinishReason.ERROR.value
                        final_error = (
                            "Inference stream terminated prematurely without terminal finish reason"
                        )
                    else:
                        final_status = "failed"
                        final_finish_reason = FinishReason.ERROR.value
                        final_error = (
                            "Inference stream terminated with unsupported finish reason: "
                            f"{final_finish_reason}"
                        )

            except asyncio.CancelledError:
                logger.warning(
                    f"Inference for message '{prepared.asst_msg_id}' cancelled by caller"
                )
                full_content = "".join(partial_tokens)
                final_status = "interrupted"
                final_finish_reason = FinishReason.CANCELLED.value
                final_error = "Inference cancelled by caller"
                if active_gen.provider and hasattr(active_gen.provider, "cancel_inference"):
                    with suppress(Exception):
                        await active_gen.provider.cancel_inference(prepared.asst_msg_id)
                await asyncio.shield(
                    cls._finalize_assistant_turn(
                        session_factory=session_factory,
                        message_id=prepared.asst_msg_id,
                        content=full_content,
                        status=final_status,
                        finish_reason=final_finish_reason,
                        usage=final_usage,
                        error_message=final_error,
                    )
                )
                raise

            except Exception as inf_exc:
                logger.error(
                    f"Inference exception during message '{prepared.asst_msg_id}': {inf_exc}"
                )
                full_content = "".join(partial_tokens)
                final_status = "failed"
                final_finish_reason = FinishReason.ERROR.value
                final_error = str(inf_exc)

            finally:
                async with cls._active_generations_guard:
                    if cls._active_generations.get(conversation_id) is active_gen:
                        cls._active_generations.pop(conversation_id, None)

            asst_dto = await cls._finalize_assistant_turn(
                session_factory=session_factory,
                message_id=prepared.asst_msg_id,
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
                user_message=prepared.user_dto,
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
