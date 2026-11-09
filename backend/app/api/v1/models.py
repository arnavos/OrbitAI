"""FastAPI routes for local AI model discovery and capabilities."""

from fastapi import APIRouter, Depends, Request

from app.ai.registry import ModelRegistry
from app.core.logging import get_correlation_id
from app.core.security import require_session_auth
from app.schemas.common import APIResponse
from app.schemas.models import ModelDescriptorResponse, ModelDiscoveryResponse

router = APIRouter(tags=["Models"], dependencies=[Depends(require_session_auth)])


@router.get(
    "",
    response_model=APIResponse[ModelDiscoveryResponse],
    summary="Discover available local AI models",
)
async def list_models(request: Request) -> APIResponse[ModelDiscoveryResponse]:
    """Query registered inference providers and discover installed local models."""
    registry: ModelRegistry | None = getattr(request.app.state, "model_registry", None)
    if registry is None:
        registry = ModelRegistry()

    discovery_result = await registry.discover_models()

    response_data = ModelDiscoveryResponse(
        models=[ModelDescriptorResponse.from_descriptor(m) for m in discovery_result.models],
        errors=discovery_result.errors,
        discovered_at=discovery_result.discovered_at,
    )

    return APIResponse[ModelDiscoveryResponse](
        success=True,
        data=response_data,
        correlation_id=get_correlation_id(),
    )
