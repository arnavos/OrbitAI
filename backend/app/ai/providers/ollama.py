"""Ollama local inference provider implementation for ORBIT."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.ai.exceptions import (
    AdmissionDeferredError,
    InferenceGenerationError,
    InferenceTimeoutError,
    InsufficientMemoryError,
    InvalidInferenceRequestError,
    ModelNotFoundError,
    ProviderUnavailableError,
)
from app.ai.provider import ModelProvider
from app.ai.resources.manager import ResourceManager
from app.ai.resources.types import AdmissionOutcome
from app.ai.types import (
    FinishReason,
    GenerationConfig,
    InferenceRequest,
    ModelAvailabilityStatus,
    ModelCapability,
    ModelDescriptor,
    ProviderHealth,
    ProviderStatus,
    ProviderType,
    TokenDelta,
    TokenUsage,
    UsageSource,
)
from app.core.config import settings
from app.core.logging import logger


class ModelLifecycleTracker:
    """Coordinates concurrent inference leases and model unloading for a single model."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.active_inferences: int = 0
        self.is_unloading: bool = False
        self.inferences_drained = asyncio.Event()
        self.inferences_drained.set()
        self.lock = asyncio.Lock()
        self.unload_future: asyncio.Future[bool] | None = None


class RequestContext:
    """Tracks active in-flight inference execution state."""

    def __init__(self, request_id: str, model_id: str, cancel_event: asyncio.Event) -> None:
        self.request_id = request_id
        self.model_id = model_id
        self.cancel_event = cancel_event
        self.response: httpx.Response | None = None


class OllamaProvider(ModelProvider):
    """Local inference provider communicating with an Ollama daemon via REST / NDJSON APIs.

    Follows the M1.1 ModelProvider abstraction and coordinates model discovery,
    streaming completions, cooperative cancellation, and verified model eviction.
    """

    DEFAULT_CONTEXT_WINDOW = 4096
    MAX_CONTEXT_CEILING = 8192
    MAX_LINE_BYTES = 65536  # 64 KB

    def __init__(
        self,
        base_url: str | None = None,
        connect_timeout: float | None = None,
        read_timeout: float | None = None,
        inter_token_timeout: float | None = None,
        keep_alive: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        provider_id: str = "ollama",
        resource_manager: ResourceManager | None = None,
    ) -> None:
        self._provider_id = provider_id
        self._base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self._connect_timeout = (
            connect_timeout if connect_timeout is not None else settings.ollama_connect_timeout
        )
        self._read_timeout = (
            read_timeout if read_timeout is not None else settings.ollama_read_timeout
        )
        self._inter_token_timeout = (
            inter_token_timeout
            if inter_token_timeout is not None
            else settings.ollama_inter_token_timeout
        )
        self._keep_alive = keep_alive or settings.ollama_keep_alive
        self._resource_manager = resource_manager

        self._external_client = http_client is not None
        self._client = http_client
        self._client_lock = asyncio.Lock()

        self._model_trackers: dict[str, ModelLifecycleTracker] = {}
        self._trackers_lock = asyncio.Lock()
        self._active_requests: dict[str, RequestContext] = {}
        self._requests_lock = asyncio.Lock()
        self._model_context_capacities: dict[str, int] = {}

    @property
    def resource_manager(self) -> ResourceManager | None:
        """Attached ResourceManager instance, if any."""
        return self._resource_manager

    @resource_manager.setter
    def resource_manager(self, manager: ResourceManager | None) -> None:
        self._resource_manager = manager

    @property
    def provider_id(self) -> str:
        """Unique identifier for this provider instance."""
        return self._provider_id

    @property
    def provider_type(self) -> ProviderType:
        """Categorical provider classification."""
        return ProviderType.OLLAMA

    @property
    def display_name(self) -> str:
        """Human-readable display name for this provider."""
        return "Ollama (Local Daemon)"

    def get_supported_capabilities(self) -> set[ModelCapability]:
        """Return the set of capabilities natively verified for this provider."""
        return {
            ModelCapability.STREAMING,
            ModelCapability.CANCELLATION,
            ModelCapability.MODEL_UNLOADING,
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Obtain or lazily instantiate the shared httpx.AsyncClient."""
        if self._client is None or self._client.is_closed:
            async with self._client_lock:
                if self._client is None or self._client.is_closed:
                    timeout = httpx.Timeout(
                        connect=self._connect_timeout,
                        read=self._read_timeout,
                        write=10.0,
                        pool=5.0,
                    )
                    self._client = httpx.AsyncClient(
                        base_url=self._base_url,
                        timeout=timeout,
                        trust_env=False,
                        follow_redirects=False,
                    )
        return self._client

    async def close(self) -> None:
        """Close managed HTTP client and release background network resources."""
        async with self._client_lock:
            if self._client and not self._client.is_closed and not self._external_client:
                await self._client.aclose()
                self._client = None

    def _resolve_native_id(self, model_id: str) -> str:
        """Extract native model ID if qualified with this provider's prefix."""
        prefix = f"{self._provider_id}/"
        if model_id.startswith(prefix):
            return model_id[len(prefix) :]
        return model_id

    async def _get_or_create_tracker(self, native_id: str) -> ModelLifecycleTracker:
        """Retrieve or create the synchronization tracker for a native model ID."""
        async with self._trackers_lock:
            if native_id not in self._model_trackers:
                self._model_trackers[native_id] = ModelLifecycleTracker(native_id)
            return self._model_trackers[native_id]

    async def _get_tracker(self, native_id: str) -> ModelLifecycleTracker | None:
        """Retrieve existing tracker without creating a new one."""
        async with self._trackers_lock:
            return self._model_trackers.get(native_id)

    def _build_options(
        self,
        config: GenerationConfig,
        verified_capacity: int | None = None,
        allocated_context: int | None = None,
    ) -> dict[str, Any]:
        """Construct sanitized options dictionary enforcing ORBIT context safeguards."""
        options: dict[str, Any] = {
            "temperature": config.temperature,
            "top_p": config.top_p,
        }

        # 1. Determine effective context window (num_ctx)
        if allocated_context is not None and allocated_context > 0:
            effective_ctx = allocated_context
        else:
            requested_ctx = config.extra_params.get("num_ctx")
            if requested_ctx is not None:
                if not isinstance(requested_ctx, int) or requested_ctx <= 0:
                    raise InvalidInferenceRequestError(
                        "num_ctx must be a strictly positive integer"
                    )
                if requested_ctx > self.MAX_CONTEXT_CEILING:
                    raise InvalidInferenceRequestError(
                        f"Requested context {requested_ctx} exceeds maximum ceiling of "
                        f"{self.MAX_CONTEXT_CEILING} tokens"
                    )
                effective_ctx = requested_ctx
            else:
                effective_ctx = self.DEFAULT_CONTEXT_WINDOW

        # Respect verified model context capacity if known
        if verified_capacity is not None and verified_capacity > 0:
            effective_ctx = min(effective_ctx, verified_capacity)

        options["num_ctx"] = effective_ctx

        # 2. Max output tokens (num_predict) & Headroom validation
        if config.max_output_tokens is not None:
            if config.max_output_tokens >= effective_ctx:
                raise InvalidInferenceRequestError(
                    f"max_output_tokens ({config.max_output_tokens}) cannot be greater than "
                    f"or equal to effective context window ({effective_ctx})"
                )
            options["num_predict"] = config.max_output_tokens

        if config.stop_sequences:
            options["stop"] = config.stop_sequences

        # 3. Controlled extra_params (prevent overriding protected options)
        protected_keys = {
            "num_ctx",
            "keep_alive",
            "stream",
            "model",
            "messages",
            "temperature",
            "top_p",
            "stop",
            "num_predict",
        }
        for k, v in config.extra_params.items():
            if k not in protected_keys:
                options[k] = v

        return options

    async def check_health(self) -> ProviderHealth:
        """Probe Ollama daemon connectivity and report status without raising exceptions."""
        start_time = time.monotonic()
        try:
            client = await self._get_client()
            resp = await client.get("/api/version")
            latency_ms = (time.monotonic() - start_time) * 1000.0

            if resp.status_code == 200:
                data = resp.json()
                version = data.get("version", "unknown")
                return ProviderHealth(
                    provider_id=self._provider_id,
                    status=ProviderStatus.ONLINE,
                    latency_ms=latency_ms,
                    message=f"Ollama {version}",
                )

            return ProviderHealth(
                provider_id=self._provider_id,
                status=ProviderStatus.DEGRADED,
                latency_ms=latency_ms,
                message=f"Unexpected health status code: {resp.status_code}",
            )
        except Exception as err:
            return ProviderHealth(
                provider_id=self._provider_id,
                status=ProviderStatus.OFFLINE,
                latency_ms=None,
                message=f"Ollama daemon unreachable: {err}",
            )

    async def discover_models(self) -> list[ModelDescriptor]:
        """Discover installed Ollama models and correlate with runtime residency info."""
        client = await self._get_client()

        try:
            resp = await client.get("/api/tags")
        except (httpx.ConnectError, httpx.ConnectTimeout) as err:
            raise ProviderUnavailableError(f"Cannot connect to Ollama daemon: {err}") from err
        except httpx.HTTPError as err:
            raise ProviderUnavailableError(f"HTTP error during model discovery: {err}") from err

        if resp.status_code != 200:
            raise ProviderUnavailableError(
                f"Ollama /api/tags returned status {resp.status_code}: {resp.text}"
            )

        tags_data = resp.json()
        models_raw = tags_data.get("models", [])

        # Probe currently resident models via /api/ps (failures captured non-fatally)
        resident_map: dict[str, dict[str, Any]] = {}
        try:
            ps_resp = await client.get("/api/ps")
            if ps_resp.status_code == 200:
                ps_data = ps_resp.json()
                raw_ps_models = ps_data.get("models", [])
                for rm in raw_ps_models:
                    m_key = rm.get("model") or rm.get("name")
                    if m_key:
                        resident_map[m_key] = rm
                if self._resource_manager is not None:
                    await self._resource_manager.reconcile_with_daemon(raw_ps_models)
        except Exception as ps_err:
            logger.debug(f"Non-fatal error reading Ollama /api/ps: {ps_err}")

        descriptors: list[ModelDescriptor] = []
        for raw in models_raw:
            raw_id = raw.get("model") or raw.get("name")
            if not raw_id:
                continue

            raw_name = raw.get("name") or raw_id
            details = raw.get("details", {})
            param_size = details.get("parameter_size")
            quant = details.get("quantization_level")
            family = details.get("family")
            context_len = details.get("context_length")

            # Validate context_length from details: must be positive integer if present
            verified_context: int | None = None
            if isinstance(context_len, int) and context_len > 0:
                verified_context = context_len
                self._model_context_capacities[raw_id] = verified_context
                if raw_name:
                    self._model_context_capacities[raw_name] = verified_context

            # Check runtime residency
            resident_info = resident_map.get(raw_id) or resident_map.get(raw_name)
            if resident_info is not None:
                status = ModelAvailabilityStatus.LOADED
                vram = resident_info.get("size_vram")
                estimated_memory = vram if (isinstance(vram, int) and vram >= 0) else None
            else:
                status = ModelAvailabilityStatus.AVAILABLE
                if self._resource_manager is not None:
                    ctx_to_est = verified_context or self.DEFAULT_CONTEXT_WINDOW
                    est = self._resource_manager.estimator.estimate_model_memory(
                        raw_id,
                        context_length=ctx_to_est,
                        arch_metadata=details,
                    )
                    estimated_memory = est.total_estimated_bytes
                else:
                    estimated_memory = None

            # Disk size kept in metadata, separated from memory
            disk_size = raw.get("size")
            metadata: dict[str, Any] = {
                "disk_size_bytes": disk_size,
                "digest": raw.get("digest"),
                "modified_at": raw.get("modified_at"),
            }

            desc = ModelDescriptor(
                id=raw_id,
                name=raw_name,
                provider_type=self.provider_type,
                provider_id=self._provider_id,
                native_model_id=raw_id,
                model_family=family,
                parameter_size=param_size,
                quantization=quant,
                context_window_capacity=verified_context,
                estimated_memory_bytes=estimated_memory,
                capabilities=self.get_supported_capabilities(),
                availability_status=status,
                provider_metadata=metadata,
            )
            descriptors.append(desc)

        return descriptors

    async def cancel_inference(self, request_id: str) -> bool:
        """Signal cooperative cancellation to an active in-flight streaming inference."""
        resp_to_close: httpx.Response | None = None
        async with self._requests_lock:
            req_ctx = self._active_requests.get(request_id)
            if not req_ctx:
                return False
            req_ctx.cancel_event.set()
            if req_ctx.response is not None and not req_ctx.response.is_closed:
                resp_to_close = req_ctx.response

        if resp_to_close is not None and not resp_to_close.is_closed:
            try:
                await resp_to_close.aclose()
            except Exception as err:
                logger.debug(f"Error closing response stream during cancellation: {err}")

        logger.info(f"Triggered cancellation for inference request '{request_id}'")
        return True

    async def _verify_model_evicted(
        self,
        native_id: str,
        max_retries: int = 10,
        delay: float = 0.2,
    ) -> bool:
        """Poll /api/ps until model is confirmed evicted from runtime memory."""
        client = await self._get_client()
        for _ in range(max_retries):
            try:
                resp = await client.get("/api/ps")
                if resp.status_code == 200:
                    data = resp.json()
                    resident = [m.get("model") or m.get("name") for m in data.get("models", [])]
                    if native_id not in resident:
                        return True
            except Exception:
                pass
            await asyncio.sleep(delay)
        return False

    async def unload_model(self, model_id: str) -> bool:
        """Evict a resident model from memory and verify eviction via /api/ps."""
        native_id = self._resolve_native_id(model_id)
        tracker = await self._get_or_create_tracker(native_id)

        is_leader = False
        wait_future: asyncio.Future[bool] | None = None
        async with tracker.lock:
            if tracker.is_unloading:
                wait_future = tracker.unload_future
            else:
                tracker.is_unloading = True
                loop = asyncio.get_running_loop()
                tracker.unload_future = loop.create_future()
                is_leader = True

        if not is_leader:
            if wait_future is not None:
                return await wait_future
            return False

        try:
            # 1. Drain in-flight inference leases (30s timeout)
            if tracker.active_inferences > 0:
                try:
                    await asyncio.wait_for(tracker.inferences_drained.wait(), timeout=30.0)
                except TimeoutError as err:
                    raise InferenceGenerationError(
                        f"Cannot unload model '{native_id}': active inference leases did "
                        "not drain in time"
                    ) from err

            # 2. Issue Ollama unload request without holding the lock during I/O
            client = await self._get_client()
            try:
                resp = await client.post(
                    "/api/generate",
                    json={"model": native_id, "keep_alive": 0},
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as err:
                raise ProviderUnavailableError(
                    f"Cannot connect to Ollama daemon to unload model '{native_id}': {err}"
                ) from err
            except httpx.HTTPError as err:
                raise InferenceGenerationError(
                    f"Failed to issue unload request to Ollama for '{native_id}': {err}"
                ) from err

            if resp.status_code == 404:
                raise ModelNotFoundError(f"Model '{native_id}' not found in Ollama daemon")
            if resp.status_code != 200:
                raise InferenceGenerationError(
                    f"Ollama unload request failed with status {resp.status_code}: {resp.text}"
                )

            # 3. Verify eviction via /api/ps
            evicted = await self._verify_model_evicted(native_id)
            if not evicted:
                raise InferenceGenerationError(
                    f"Model '{native_id}' unload reported by daemon but model remains resident"
                )

            if self._resource_manager is not None:
                await self._resource_manager.clear_model_resident(native_id)

            tracker.unload_future.set_result(True)
            logger.info(f"Successfully evicted model '{native_id}' from Ollama memory")
            return True
        except Exception as exc:
            if not tracker.unload_future.done():
                tracker.unload_future.set_exception(exc)
            raise
        finally:
            async with tracker.lock:
                tracker.is_unloading = False
                tracker.unload_future = None

    async def stream_inference(self, request: InferenceRequest) -> AsyncIterator[TokenDelta]:
        """Stream token deltas from Ollama with leases, limits, safeguards, and cancellation."""
        native_id = self._resolve_native_id(request.model_id)

        if self._resource_manager is not None:
            req_ctx = request.config.extra_params.get("num_ctx")
            async with self._resource_manager.reserve_inference(
                model_id=native_id,
                request_id=request.request_id,
                requested_context=req_ctx,
                provider_unload_callable=self.unload_model,
            ) as admission:
                if admission.outcome == AdmissionOutcome.DEFER:
                    raise AdmissionDeferredError(admission.reason)
                if admission.outcome in (AdmissionOutcome.REJECT, AdmissionOutcome.REQUIRE_UNLOAD):
                    raise InsufficientMemoryError(admission.reason)

                inner = self._stream_with_tracker(
                    request=request,
                    native_id=native_id,
                    allocated_context=admission.allocated_context,
                )
                try:
                    async for delta in inner:
                        yield delta
                finally:
                    await inner.aclose()
        else:
            inner = self._stream_with_tracker(
                request=request,
                native_id=native_id,
                allocated_context=None,
            )
            try:
                async for delta in inner:
                    yield delta
            finally:
                await inner.aclose()

    async def _stream_with_tracker(
        self,
        request: InferenceRequest,
        native_id: str,
        allocated_context: int | None,
    ) -> AsyncIterator[TokenDelta]:
        """Stream inference tokens under tracker leases and context constraints."""
        tracker = await self._get_or_create_tracker(native_id)
        cancel_event = asyncio.Event()

        # 1. Acquire lease under lock
        async with tracker.lock:
            if tracker.is_unloading:
                raise InferenceGenerationError(
                    f"Model '{native_id}' is currently unloading and cannot accept new inference "
                    "requests"
                )
            tracker.active_inferences += 1
            tracker.inferences_drained.clear()

        # Register active request context
        req_ctx = RequestContext(
            request_id=request.request_id,
            model_id=native_id,
            cancel_event=cancel_event,
        )
        async with self._requests_lock:
            self._active_requests[request.request_id] = req_ctx

        confirmed_loaded = False
        try:
            # Check early cancellation before network setup
            if cancel_event.is_set():
                yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                return

            # 2. Build payload with context safeguards
            verified_capacity = self._model_context_capacities.get(native_id)
            options = self._build_options(
                request.config,
                verified_capacity=verified_capacity,
                allocated_context=allocated_context,
            )
            messages_payload = [
                {
                    "role": m.role.value if hasattr(m.role, "value") else str(m.role),
                    "content": m.content,
                }
                for m in request.messages
            ]
            body_payload = {
                "model": native_id,
                "messages": messages_payload,
                "stream": True,
                "options": options,
                "keep_alive": self._keep_alive,
            }

            client = await self._get_client()

            # 3. Stream from Ollama /api/chat
            try:
                async with client.stream(
                    "POST",
                    "/api/chat",
                    json=body_payload,
                    timeout=httpx.Timeout(
                        connect=self._connect_timeout,
                        read=self._read_timeout,
                        write=10.0,
                        pool=5.0,
                    ),
                ) as response:
                    req_ctx.response = response
                    if response.status_code == 404:
                        raise ModelNotFoundError(
                            f"Model '{native_id}' not found in provider '{self._provider_id}'"
                        )
                    if response.status_code == 503:
                        err_body = await response.aread()
                        raise ProviderUnavailableError(
                            "Ollama service unavailable (HTTP 503): "
                            f"{err_body.decode(errors='replace')}"
                        )
                    if response.status_code != 200:
                        err_body = await response.aread()
                        raise InferenceGenerationError(
                            f"Ollama inference error (HTTP {response.status_code}): "
                            f"{err_body.decode(errors='replace')}"
                        )

                    buffer = bytearray()
                    stream_iter = response.aiter_bytes()
                    received_terminal = False

                    while True:
                        if cancel_event.is_set():
                            yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                            return

                        try:
                            chunk = await asyncio.wait_for(
                                anext(stream_iter),
                                timeout=self._inter_token_timeout,
                            )
                        except StopAsyncIteration:
                            break
                        except TimeoutError as err:
                            if cancel_event.is_set():
                                yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                                return
                            raise InferenceTimeoutError(
                                f"Inference inter-token timeout ({self._inter_token_timeout}s) "
                                "exceeded"
                            ) from err

                        if not confirmed_loaded and self._resource_manager is not None:
                            confirmed_loaded = True
                            await self._resource_manager.confirm_model_loaded(
                                native_id,
                                verified_context=options["num_ctx"],
                            )

                        buffer.extend(chunk)

                        while b"\n" in buffer:
                            line, _, rest = buffer.partition(b"\n")
                            if len(line) > self.MAX_LINE_BYTES:
                                raise InferenceGenerationError(
                                    "NDJSON line exceeded maximum allowed length of "
                                    f"{self.MAX_LINE_BYTES} bytes"
                                )
                            buffer = bytearray(rest)

                            line_str = line.decode("utf-8").strip()
                            if not line_str:
                                continue

                            try:
                                item = json.loads(line_str)
                            except json.JSONDecodeError as err:
                                raise InferenceGenerationError(
                                    f"Failed to parse NDJSON response from Ollama: {err}"
                                ) from err

                            if "error" in item:
                                err_msg = item["error"]
                                if "not found" in err_msg.lower():
                                    raise ModelNotFoundError(
                                        f"Model '{native_id}' not found: {err_msg}"
                                    )
                                raise InferenceGenerationError(
                                    f"Ollama generation error: {err_msg}"
                                )

                            if cancel_event.is_set():
                                yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                                return

                            msg_obj = item.get("message", {})
                            delta_text = msg_obj.get("content", "")
                            is_done = item.get("done", False)

                            if not is_done:
                                if delta_text:
                                    yield TokenDelta(text=delta_text)
                            else:
                                received_terminal = True
                                done_reason = item.get("done_reason", "stop")
                                finish_reason = (
                                    FinishReason.STOP
                                    if done_reason == "stop"
                                    else FinishReason.LENGTH
                                    if done_reason == "length"
                                    else FinishReason.STOP
                                )
                                prompt_tokens = item.get("prompt_eval_count")
                                completion_tokens = item.get("eval_count")
                                usage = None
                                if prompt_tokens is not None or completion_tokens is not None:
                                    usage = TokenUsage(
                                        prompt_tokens=prompt_tokens,
                                        completion_tokens=completion_tokens,
                                        source=UsageSource.REPORTED,
                                    )
                                yield TokenDelta(
                                    text=delta_text,
                                    finish_reason=finish_reason,
                                    usage=usage,
                                )
                                return

                        # Guard incomplete, unterminated line from growing beyond 64 KB
                        if len(buffer) > self.MAX_LINE_BYTES:
                            raise InferenceGenerationError(
                                "NDJSON line exceeded maximum allowed length of "
                                f"{self.MAX_LINE_BYTES} bytes"
                            )

                    # Process any remaining un-delimited line in buffer on stream completion
                    if buffer:
                        if len(buffer) > self.MAX_LINE_BYTES:
                            raise InferenceGenerationError(
                                "NDJSON line exceeded maximum allowed length of "
                                f"{self.MAX_LINE_BYTES} bytes"
                            )
                        line_str = buffer.decode("utf-8").strip()
                        if line_str:
                            try:
                                item = json.loads(line_str)
                            except json.JSONDecodeError as err:
                                raise InferenceGenerationError(
                                    f"Failed to parse NDJSON response from Ollama: {err}"
                                ) from err

                            if "error" in item:
                                err_msg = item["error"]
                                if "not found" in err_msg.lower():
                                    raise ModelNotFoundError(
                                        f"Model '{native_id}' not found: {err_msg}"
                                    )
                                raise InferenceGenerationError(
                                    f"Ollama generation error: {err_msg}"
                                )

                            if cancel_event.is_set():
                                yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                                return

                            msg_obj = item.get("message", {})
                            delta_text = msg_obj.get("content", "")
                            if item.get("done", False):
                                received_terminal = True
                                done_reason = item.get("done_reason", "stop")
                                finish_reason = (
                                    FinishReason.STOP
                                    if done_reason == "stop"
                                    else FinishReason.LENGTH
                                    if done_reason == "length"
                                    else FinishReason.STOP
                                )
                                prompt_tokens = item.get("prompt_eval_count")
                                completion_tokens = item.get("eval_count")
                                usage = None
                                if prompt_tokens is not None or completion_tokens is not None:
                                    usage = TokenUsage(
                                        prompt_tokens=prompt_tokens,
                                        completion_tokens=completion_tokens,
                                        source=UsageSource.REPORTED,
                                    )
                                yield TokenDelta(
                                    text=delta_text,
                                    finish_reason=finish_reason,
                                    usage=usage,
                                )
                                return

                    if cancel_event.is_set():
                        yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                        return

                    if not received_terminal:
                        raise InferenceGenerationError(
                            "Inference stream terminated unexpectedly before completion "
                            "(empty stream or premature EOF)"
                        )

            except (httpx.ConnectError, httpx.ConnectTimeout) as err:
                if cancel_event.is_set():
                    yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                    return
                raise ProviderUnavailableError(f"Ollama daemon unreachable: {err}") from err
            except httpx.ReadTimeout as err:
                if cancel_event.is_set():
                    yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                    return
                raise InferenceTimeoutError(f"Ollama inference read timeout: {err}") from err
            except (httpx.HTTPError, httpx.StreamError) as err:
                if cancel_event.is_set():
                    yield TokenDelta(text="", finish_reason=FinishReason.CANCELLED)
                    return
                raise InferenceGenerationError(
                    f"HTTP transport error during inference: {err}"
                ) from err

        except asyncio.CancelledError:
            logger.info(f"Inference request '{request.request_id}' cancelled by task")
            raise
        except GeneratorExit:
            logger.info(f"Inference stream '{request.request_id}' closed by generator consumer")
            return
        finally:
            # Clean up active request context
            async with self._requests_lock:
                self._active_requests.pop(request.request_id, None)

            # Release lease under lock
            async with tracker.lock:
                tracker.active_inferences = max(0, tracker.active_inferences - 1)
                if tracker.active_inferences == 0:
                    tracker.inferences_drained.set()
