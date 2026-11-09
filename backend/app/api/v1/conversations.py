"""FastAPI routes for conversation thread management and synchronous messaging."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.exceptions import ConversationNotFoundError
from app.core.logging import get_correlation_id
from app.core.security import require_session_auth
from app.db.session import async_session_factory, get_db
from app.models.conversation import Conversation
from app.schemas.common import APIResponse
from app.schemas.conversation import (
    CancelTurnResponse,
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdate,
    MessageListResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.services.conversation_service import ConversationService

router = APIRouter(
    prefix="/conversations",
    tags=["Conversations"],
    dependencies=[Depends(require_session_auth)],
)


@router.post(
    "",
    response_model=APIResponse[ConversationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new conversation thread",
)
async def create_conversation(
    payload: ConversationCreate,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[ConversationResponse]:
    """Initialize and persist a new conversation thread."""
    conv = await ConversationService.create_conversation(session=db, payload=payload)
    return APIResponse[ConversationResponse](
        success=True,
        data=conv,
        correlation_id=get_correlation_id(),
    )


@router.get(
    "",
    response_model=APIResponse[ConversationListResponse],
    summary="List conversation threads",
)
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[ConversationListResponse]:
    """Retrieve paginated conversation threads ordered by last updated timestamp."""
    items, total = await ConversationService.list_conversations(
        session=db, limit=limit, offset=offset
    )
    return APIResponse[ConversationListResponse](
        success=True,
        data=ConversationListResponse(conversations=items, total=total),
        correlation_id=get_correlation_id(),
    )


@router.get(
    "/{conversation_id}",
    response_model=APIResponse[ConversationResponse],
    summary="Retrieve conversation details",
)
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[ConversationResponse]:
    """Retrieve metadata and total message count for a specific conversation thread."""
    conv = await ConversationService.get_conversation(session=db, conversation_id=conversation_id)
    return APIResponse[ConversationResponse](
        success=True,
        data=conv,
        correlation_id=get_correlation_id(),
    )


@router.patch(
    "/{conversation_id}",
    response_model=APIResponse[ConversationResponse],
    summary="Update conversation metadata",
)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[ConversationResponse]:
    """Update title or default model for an existing conversation thread."""
    updated = await ConversationService.update_conversation(
        session=db, conversation_id=conversation_id, payload=payload
    )
    return APIResponse[ConversationResponse](
        success=True,
        data=updated,
        correlation_id=get_correlation_id(),
    )


@router.delete(
    "/{conversation_id}",
    response_model=APIResponse[bool],
    summary="Delete a conversation thread",
)
async def delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
) -> APIResponse[bool]:
    """Cascade delete a conversation thread and all contained turns."""
    deleted = await ConversationService.delete_conversation(
        session=db, conversation_id=conversation_id
    )
    return APIResponse[bool](
        success=True,
        data=deleted,
        correlation_id=get_correlation_id(),
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=APIResponse[MessageListResponse],
    summary="List messages in a conversation",
)
async def list_messages(
    conversation_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    before_sequence: int | None = Query(default=None, ge=0),
    db: AsyncSession = Depends(get_db),
) -> APIResponse[MessageListResponse]:
    """Retrieve ordered conversational turns for a thread."""
    messages, total = await ConversationService.list_messages(
        session=db,
        conversation_id=conversation_id,
        limit=limit,
        before_sequence=before_sequence,
    )
    return APIResponse[MessageListResponse](
        success=True,
        data=MessageListResponse(messages=messages, total=total),
        correlation_id=get_correlation_id(),
    )


@router.post(
    "/{conversation_id}/messages",
    response_model=APIResponse[SendMessageResponse],
    summary="Send user message and receive assistant response synchronously",
)
async def send_message(
    conversation_id: str,
    payload: SendMessageRequest,
    request: Request,
) -> APIResponse[SendMessageResponse]:
    """Synchronously submit a user prompt, execute model inference, and return both turns."""
    registry = getattr(request.app.state, "model_registry", None)
    if registry is None:
        from app.ai.registry import ModelRegistry

        registry = ModelRegistry()

    resource_manager = getattr(request.app.state, "resource_manager", None)
    session_factory = getattr(request.app.state, "session_factory", async_session_factory)

    result = await ConversationService.send_message(
        session_factory=session_factory,
        conversation_id=conversation_id,
        payload=payload,
        registry=registry,
        resource_manager=resource_manager,
    )

    return APIResponse[SendMessageResponse](
        success=True,
        data=result,
        correlation_id=get_correlation_id(),
    )


@router.post(
    "/{conversation_id}/messages/stream",
    summary="Send user message and stream assistant response via Server-Sent Events",
)
async def send_message_stream(
    conversation_id: str,
    payload: SendMessageRequest,
    request: Request,
) -> StreamingResponse:
    """Submit user prompt and stream model inference token deltas over Server-Sent Events."""
    if not payload.content.strip():
        raise HTTPException(
            status_code=getattr(
                status, "HTTP_422_UNPROCESSABLE_CONTENT", status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail="User message content cannot be empty",
        )

    session_factory = getattr(request.app.state, "session_factory", async_session_factory)
    async with session_factory() as session:
        conv = await session.get(Conversation, conversation_id)
        if not conv:
            raise ConversationNotFoundError(f"Conversation '{conversation_id}' not found")

    registry = getattr(request.app.state, "model_registry", None)
    if registry is None:
        from app.ai.registry import ModelRegistry

        registry = ModelRegistry()

    resource_manager = getattr(request.app.state, "resource_manager", None)

    event_generator = ConversationService.send_message_stream(
        session_factory=session_factory,
        conversation_id=conversation_id,
        payload=payload,
        registry=registry,
        resource_manager=resource_manager,
        disconnect_check=request.is_disconnected,
    )

    return StreamingResponse(
        event_generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{conversation_id}/cancel",
    response_model=APIResponse[CancelTurnResponse],
    summary="Explicitly cancel an active inference generation on a conversation",
)
async def cancel_turn(
    conversation_id: str,
    request: Request,
) -> APIResponse[CancelTurnResponse]:
    """Explicitly cancel active inference turn for the specified conversation thread."""
    session_factory = getattr(request.app.state, "session_factory", async_session_factory)
    cancelled, message_id, detail = await ConversationService.cancel_turn(
        session_factory=session_factory,
        conversation_id=conversation_id,
    )
    return APIResponse[CancelTurnResponse](
        success=True,
        data=CancelTurnResponse(
            cancelled=cancelled,
            message_id=message_id,
            detail=detail,
        ),
        correlation_id=get_correlation_id(),
    )
