"""Unit and integration tests for OllamaProvider.

Includes mocked contract, parsing, error, lifecycle, and cancellation tests,
as well as live tests against the local Ollama daemon when available.
"""

import asyncio
import json

import httpx
import pytest

from app.ai.exceptions import (
    InferenceGenerationError,
    InvalidInferenceRequestError,
    ModelNotFoundError,
    ProviderUnavailableError,
)
from app.ai.providers.ollama import OllamaProvider
from app.ai.types import (
    ChatMessage,
    FinishReason,
    GenerationConfig,
    InferenceRequest,
    MessageRole,
    ModelAvailabilityStatus,
    ModelCapability,
    ProviderStatus,
    ProviderType,
)


def _make_transport_handler(handler_func):
    """Helper creating an httpx.MockTransport with the given handler."""
    return httpx.MockTransport(handler_func)


def test_ollama_provider_metadata_and_capabilities():
    """Verify provider metadata and strictly advertised capabilities."""
    provider = OllamaProvider()
    assert provider.provider_id == "ollama"
    assert provider.provider_type == ProviderType.OLLAMA
    assert provider.display_name == "Ollama (Local Daemon)"

    caps = provider.get_supported_capabilities()
    assert ModelCapability.STREAMING in caps
    assert ModelCapability.CANCELLATION in caps
    assert ModelCapability.MODEL_UNLOADING in caps
    assert ModelCapability.VISION not in caps
    assert ModelCapability.TOOL_CALLING not in caps

    assert provider.supports_unloading() is True
    assert provider.supports_cancellation() is True


@pytest.mark.asyncio
async def test_ollama_check_health_online():
    """Verify check_health returns ONLINE when daemon responds with version."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/version"
        return httpx.Response(200, json={"version": "0.40.14"})

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    health = await provider.check_health()
    assert health.status == ProviderStatus.ONLINE
    assert "0.40.14" in (health.message or "")
    assert health.latency_ms is not None
    assert health.latency_ms >= 0.0


@pytest.mark.asyncio
async def test_ollama_check_health_offline():
    """Verify check_health returns OFFLINE without raising exception when daemon is unreachable."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    health = await provider.check_health()
    assert health.status == ProviderStatus.OFFLINE
    assert "Connection refused" in (health.message or "")
    assert health.latency_ms is None


@pytest.mark.asyncio
async def test_ollama_check_health_degraded():
    """Verify check_health returns DEGRADED on unexpected HTTP status."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Bad Gateway")

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    health = await provider.check_health()
    assert health.status == ProviderStatus.DEGRADED
    assert "502" in (health.message or "")


@pytest.mark.asyncio
async def test_ollama_discover_models_success():
    """Verify discovery accurately parses tags, correlates /api/ps, and separates memory from disk."""
    tags_payload = {
        "models": [
            {
                "name": "llama3.2:3b",
                "model": "llama3.2:3b",
                "size": 2019393189,
                "digest": "a80c4f17",
                "details": {
                    "family": "llama",
                    "parameter_size": "3.2B",
                    "quantization_level": "Q4_K_M",
                    "context_length": 131072,
                },
            },
            {
                "name": "org/custom-model:latest",
                "model": "org/custom-model:latest",
                "size": 4000000000,
                "details": {
                    "family": "custom",
                    # context_length missing
                },
            },
        ]
    }
    ps_payload = {
        "models": [
            {
                "name": "llama3.2:3b",
                "size_vram": 2561000078,
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags_payload)
        if request.url.path == "/api/ps":
            return httpx.Response(200, json=ps_payload)
        return httpx.Response(404)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    models = await provider.discover_models()
    assert len(models) == 2

    # Model 1: Resident in /api/ps
    m1 = models[0]
    assert m1.id == "llama3.2:3b"
    assert m1.native_model_id == "llama3.2:3b"
    assert m1.context_window_capacity == 131072
    assert m1.availability_status == ModelAvailabilityStatus.LOADED
    assert m1.estimated_memory_bytes == 2561000078
    assert m1.provider_metadata["disk_size_bytes"] == 2019393189

    # Model 2: Not in /api/ps, slash-containing native ID
    m2 = models[1]
    assert m2.id == "org/custom-model:latest"
    assert m2.native_model_id == "org/custom-model:latest"
    assert m2.context_window_capacity is None  # Unknown preserved
    assert m2.availability_status == ModelAvailabilityStatus.AVAILABLE
    assert m2.estimated_memory_bytes is None


@pytest.mark.asyncio
async def test_ollama_discover_models_daemon_offline():
    """Verify discovery raises ProviderUnavailableError when daemon is offline."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("Connect timeout")

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    with pytest.raises(ProviderUnavailableError, match="Cannot connect to Ollama daemon"):
        await provider.discover_models()


def test_ollama_context_safeguards():
    """Verify context ceiling, output headroom, and parameter protection."""
    provider = OllamaProvider()

    # 1. Default context: 4096
    cfg = GenerationConfig()
    opts = provider._build_options(cfg)
    assert opts["num_ctx"] == 4096

    # 2. Explicit valid override
    cfg2 = GenerationConfig(extra_params={"num_ctx": 2048})
    opts2 = provider._build_options(cfg2)
    assert opts2["num_ctx"] == 2048

    # 3. Context ceiling rejection (> 8192)
    cfg3 = GenerationConfig(extra_params={"num_ctx": 16384})
    with pytest.raises(InvalidInferenceRequestError, match="exceeds maximum ceiling of 8192"):
        provider._build_options(cfg3)

    # 4. Non-positive context rejection
    cfg4 = GenerationConfig(extra_params={"num_ctx": 0})
    with pytest.raises(InvalidInferenceRequestError, match="strictly positive integer"):
        provider._build_options(cfg4)

    # 5. Output headroom rejection: max_output_tokens >= effective_ctx
    cfg5 = GenerationConfig(max_output_tokens=4096)
    with pytest.raises(InvalidInferenceRequestError, match="cannot be greater than or equal"):
        provider._build_options(cfg5)

    # 6. Protected parameters cannot be overridden
    cfg6 = GenerationConfig(extra_params={"keep_alive": "100m", "safe_param": "allowed"})
    opts6 = provider._build_options(cfg6)
    assert "keep_alive" not in opts6
    assert opts6.get("safe_param") == "allowed"


@pytest.mark.asyncio
async def test_ollama_streaming_inference_success():
    """Verify incremental token streaming, terminal delta, and token usage accounting."""
    ndjson_body = (
        b'{"message":{"role":"assistant","content":"Hello"},"done":false}\n'
        b'{"message":{"role":"assistant","content":" world"},"done":false}\n'
        b'{"message":{"role":"assistant","content":"!"},"done":true,"done_reason":"stop",'
        b'"prompt_eval_count":10,"eval_count":3}\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["model"] == "llama3.2:3b"
        assert payload["stream"] is True
        return httpx.Response(200, content=ndjson_body)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="ollama/llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="Say hi")],
    )

    deltas = []
    async for delta in provider.stream_inference(req):
        deltas.append(delta)

    assert len(deltas) == 3
    assert deltas[0].text == "Hello"
    assert deltas[0].finish_reason is None
    assert deltas[1].text == " world"
    assert deltas[1].finish_reason is None

    # Terminal chunk
    assert deltas[2].text == "!"
    assert deltas[2].finish_reason == FinishReason.STOP
    assert deltas[2].usage is not None
    assert deltas[2].usage.prompt_tokens == 10
    assert deltas[2].usage.completion_tokens == 3
    assert deltas[2].usage.total_tokens == 13


@pytest.mark.asyncio
async def test_ollama_streaming_line_too_long():
    """Verify NDJSON line exceeding 64 KB raises InferenceGenerationError."""
    oversized_line = b'{"message":{"content":"' + (b"A" * 66000) + b'"},"done":false}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized_line)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    with pytest.raises(InferenceGenerationError, match="maximum allowed length"):
        async for _ in provider.stream_inference(req):
            pass


@pytest.mark.asyncio
async def test_ollama_streaming_inband_error():
    """Verify Ollama in-band error payload maps to appropriate ORBIT error."""
    error_body = b'{"error":"model \'nonexistent\' not found"}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=error_body)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="nonexistent",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    with pytest.raises(ModelNotFoundError, match="not found"):
        async for _ in provider.stream_inference(req):
            pass


@pytest.mark.asyncio
async def test_ollama_streaming_cancellation():
    """Verify cooperative cancellation terminates stream and yields CANCELLED."""

    async def slow_stream_content():
        yield b'{"message":{"role":"assistant","content":"Part1"},"done":false}\n'
        await asyncio.sleep(0.5)
        yield b'{"message":{"role":"assistant","content":"Part2"},"done":false}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=slow_stream_content())

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        request_id="cancel-test-1",
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    stream = provider.stream_inference(req)
    first_chunk = await anext(stream)
    assert first_chunk.text == "Part1"

    # Trigger cancellation
    cancelled = await provider.cancel_inference("cancel-test-1")
    assert cancelled is True

    # Next chunk must be CANCELLED terminal delta
    terminal_chunk = await anext(stream)
    assert terminal_chunk.finish_reason == FinishReason.CANCELLED


@pytest.mark.asyncio
async def test_ollama_cancellation_while_waiting_for_network_data():
    """Verify cancelling while stream is actively awaiting network data terminates cleanly."""
    waiting_on_network = asyncio.Event()

    class BlockableByteStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = asyncio.Event()

        async def __aiter__(self):
            # First token is emitted immediately
            yield b'{"message":{"role":"assistant","content":"hello"},"done":false}\n'
            # Signal that caller is now waiting on the next network chunk
            waiting_on_network.set()
            # Wait until closed or cancelled
            await self.closed.wait()
            raise httpx.StreamClosed()

        async def aclose(self):
            self.closed.set()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=BlockableByteStream())

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        request_id="network-wait-cancel",
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    stream = provider.stream_inference(req)
    # Read first token
    first_delta = await anext(stream)
    assert first_delta.text == "hello"

    # Start task to read next delta (this task will block waiting on network data)
    read_task = asyncio.create_task(anext(stream))

    # Wait until generator has actually entered the network wait
    await waiting_on_network.wait()

    # Now trigger cancellation while read_task is actively blocked
    cancelled = await provider.cancel_inference("network-wait-cancel")
    assert cancelled is True

    # read_task must unblock and receive CANCELLED finish reason
    cancel_delta = await asyncio.wait_for(read_task, timeout=2.0)
    assert cancel_delta.finish_reason == FinishReason.CANCELLED

    # Stream is at terminal state; exhausting or closing it invokes generator cleanup
    with pytest.raises(StopAsyncIteration):
        await anext(stream)

    # Verify tracker leases were freed
    tracker = await provider._get_or_create_tracker("llama3.2:3b")
    assert tracker.active_inferences == 0
    assert tracker.inferences_drained.is_set()


@pytest.mark.asyncio
async def test_ollama_lifecycle_blocks_inference_during_unload():
    """Verify inference leases are rejected while model is in unloading state."""
    provider = OllamaProvider()
    tracker = await provider._get_or_create_tracker("llama3.2:3b")

    # Manually set is_unloading
    async with tracker.lock:
        tracker.is_unloading = True

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    with pytest.raises(InferenceGenerationError, match="currently unloading"):
        async for _ in provider.stream_inference(req):
            pass

    async with tracker.lock:
        tracker.is_unloading = False


@pytest.mark.asyncio
async def test_ollama_model_unloading_success():
    """Verify model unloading issues keep_alive: 0 and verifies eviction with /api/ps."""
    ps_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ps_calls
        if request.url.path == "/api/generate":
            payload = json.loads(request.content.decode("utf-8"))
            assert payload["model"] == "llama3.2:3b"
            assert payload["keep_alive"] == 0
            return httpx.Response(200, json={"done": True, "done_reason": "unload"})
        if request.url.path == "/api/ps":
            ps_calls += 1
            # First call returns model resident, second call returns evicted
            if ps_calls == 1:
                return httpx.Response(200, json={"models": [{"model": "llama3.2:3b"}]})
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    result = await provider.unload_model("ollama/llama3.2:3b")
    assert result is True
    assert ps_calls >= 2


@pytest.mark.asyncio
async def test_ollama_concurrent_unloads_share_future():
    """Verify concurrent unload requests on the same model coordinate deterministically."""
    unload_started = asyncio.Event()

    async def delayed_generate(request: httpx.Request) -> httpx.Response:
        unload_started.set()
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"done": True, "done_reason": "unload"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/generate":
            return httpx.Response(200, json={"done": True, "done_reason": "unload"})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    # Launch two concurrent unloads
    task1 = asyncio.create_task(provider.unload_model("llama3.2:3b"))
    task2 = asyncio.create_task(provider.unload_model("llama3.2:3b"))

    r1, r2 = await asyncio.gather(task1, task2)
    assert r1 is True
    assert r2 is True


@pytest.mark.asyncio
async def test_ollama_streaming_premature_eof_raises_error():
    """Verify premature stream termination before done=True raises InferenceGenerationError."""
    # Chunk with partial token stream and no done: true
    ndjson = b'{"message":{"role":"assistant","content":"token1"},"done":false}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    with pytest.raises(InferenceGenerationError, match="terminated unexpectedly before completion"):
        async for _ in provider.stream_inference(req):
            pass


@pytest.mark.asyncio
async def test_ollama_streaming_empty_stream_raises_error():
    """Verify completely empty stream raises InferenceGenerationError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    with pytest.raises(InferenceGenerationError, match="terminated unexpectedly before completion"):
        async for _ in provider.stream_inference(req):
            pass


@pytest.mark.asyncio
async def test_ollama_streaming_un_delimited_final_chunk_parsed():
    """Verify stream ending without a trailing newline parses the terminal chunk correctly."""
    # Note: no trailing \n after the last JSON object
    ndjson = (
        b'{"message":{"role":"assistant","content":"Hi"},"done":false}\n'
        b'{"message":{"role":"assistant","content":"!"},"done":true,"done_reason":"stop"}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=ndjson)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    deltas = []
    async for delta in provider.stream_inference(req):
        deltas.append(delta)

    assert len(deltas) == 2
    assert deltas[0].text == "Hi"
    assert deltas[1].text == "!"
    assert deltas[1].finish_reason == FinishReason.STOP


@pytest.mark.asyncio
async def test_ollama_streaming_multiline_chunk_exceeding_64kb_succeeds():
    """Verify single network chunk > 64 KB containing multiple valid lines <= 64 KB succeeds."""
    # Two valid lines, each ~35 KB, aggregate chunk size ~70 KB (> 64 KB limit)
    line1 = (
        json.dumps(
            {"message": {"role": "assistant", "content": "A" * 35000}, "done": False}
        ).encode()
        + b"\n"
    )
    line2 = (
        json.dumps(
            {
                "message": {"role": "assistant", "content": "B" * 35000},
                "done": True,
                "done_reason": "stop",
            }
        ).encode()
        + b"\n"
    )
    single_large_chunk = line1 + line2
    assert len(single_large_chunk) > 65536
    assert len(line1) <= 65536
    assert len(line2) <= 65536

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=single_large_chunk)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    deltas = []
    async for delta in provider.stream_inference(req):
        deltas.append(delta)

    assert len(deltas) == 2
    assert deltas[0].text == "A" * 35000
    assert deltas[1].text == "B" * 35000
    assert deltas[1].finish_reason == FinishReason.STOP


@pytest.mark.asyncio
async def test_ollama_streaming_single_line_exceeding_64kb_raises_error():
    """Verify single NDJSON line exceeding 64 KB is rejected with InferenceGenerationError."""
    # A single valid JSON line of 70 KB ending with \n
    oversized_line = (
        json.dumps(
            {"message": {"role": "assistant", "content": "X" * 70000}, "done": False}
        ).encode()
        + b"\n"
    )
    assert len(oversized_line) > 65536

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=oversized_line)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    with pytest.raises(InferenceGenerationError, match="maximum allowed length"):
        async for _ in provider.stream_inference(req):
            pass


@pytest.mark.asyncio
async def test_ollama_streaming_unterminated_line_exceeding_64kb_across_chunks_raises_error():
    """Verify unterminated line growing beyond 64 KB across multiple chunks is rejected."""

    # Two chunks with no newline, each 35 KB, aggregate 70 KB
    async def chunk_generator():
        yield b"A" * 35000
        yield b"B" * 35000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunk_generator())

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    with pytest.raises(InferenceGenerationError, match="maximum allowed length"):
        async for _ in provider.stream_inference(req):
            pass


@pytest.mark.asyncio
async def test_ollama_streaming_records_split_across_arbitrary_chunk_boundaries():
    """Verify records split across arbitrary chunk boundaries are parsed cleanly."""
    rec1 = (
        json.dumps({"message": {"role": "assistant", "content": "part1"}, "done": False}).encode()
        + b"\n"
    )
    rec2 = (
        json.dumps({"message": {"role": "assistant", "content": "part2"}, "done": False}).encode()
        + b"\n"
    )
    rec3 = (
        json.dumps({"message": {"role": "assistant", "content": "part3"}, "done": False}).encode()
        + b"\n"
    )
    rec4 = (
        json.dumps({"message": {"role": "assistant", "content": "part4"}, "done": False}).encode()
        + b"\n"
    )
    rec5 = (
        json.dumps(
            {
                "message": {"role": "assistant", "content": "part5"},
                "done": True,
                "done_reason": "stop",
            }
        ).encode()
        + b"\n"
    )
    full_stream = rec1 + rec2 + rec3 + rec4 + rec5

    # Split into arbitrary chunks of 23 bytes (cutting through keys, values, and newlines)
    chunk_size = 23
    chunks = [full_stream[i : i + chunk_size] for i in range(0, len(full_stream), chunk_size)]
    assert len(chunks) > 5

    async def chunk_gen():
        for ch in chunks:
            yield ch

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunk_gen())

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    deltas = []
    async for delta in provider.stream_inference(req):
        deltas.append(delta)

    assert len(deltas) == 5
    assert [d.text for d in deltas] == ["part1", "part2", "part3", "part4", "part5"]
    assert deltas[-1].finish_reason == FinishReason.STOP


@pytest.mark.asyncio
async def test_ollama_streaming_unbounded_line_without_newline_raises_error():
    """Verify buffer growing beyond 64 KB without newlines raises InferenceGenerationError."""
    # Single chunk sent without newlines exceeding 64 KB
    chunk1 = b"A" * 70000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunk1)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    with pytest.raises(InferenceGenerationError, match="maximum allowed length"):
        async for _ in provider.stream_inference(req):
            pass


@pytest.mark.asyncio
async def test_ollama_streaming_503_maps_to_provider_unavailable():
    """Verify HTTP 503 from Ollama maps to ProviderUnavailableError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable / Loading")

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    with pytest.raises(ProviderUnavailableError, match="HTTP 503"):
        async for _ in provider.stream_inference(req):
            pass


@pytest.mark.asyncio
async def test_ollama_verified_context_capacity_clamping():
    """Verify that verified context capacity from discovery clamps effective context window."""
    tags_payload = {
        "models": [
            {
                "name": "small-ctx-model:latest",
                "details": {
                    "context_length": 2048,
                },
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=tags_payload)
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    # Discover models to populate context capacities
    await provider.discover_models()

    # Now verify options built for small-ctx-model: default 4096 is clamped to 2048!
    opts = provider._build_options(
        GenerationConfig(),
        verified_capacity=provider._model_context_capacities.get("small-ctx-model:latest"),
    )
    assert opts["num_ctx"] == 2048


@pytest.mark.asyncio
async def test_ollama_generator_abandonment_cleans_up_lease():
    """Verify that abandoning the async generator properly releases the active inference lease."""

    async def slow_stream():
        for i in range(10):
            yield f'{{"message":{{"content":"chunk{i}"}},"done":false}}\n'.encode()
            await asyncio.sleep(0.05)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=slow_stream())

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        request_id="abandon-req-1",
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    stream = provider.stream_inference(req)
    # Read only 1 chunk and break (abandoning generator)
    async for delta in stream:
        assert delta.text == "chunk0"
        break
    await stream.aclose()

    # Generator is closed; check that lease was freed
    tracker = await provider._get_or_create_tracker("llama3.2:3b")
    assert tracker.active_inferences == 0
    assert tracker.inferences_drained.is_set()


@pytest.mark.asyncio
async def test_ollama_task_cancellation_cleans_up_lease():
    """Verify cancelling the asyncio task running inference re-raises and frees lease."""

    async def infinite_stream():
        while True:
            yield b'{"message":{"content":"tick"},"done":false}\n'
            await asyncio.sleep(0.05)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=infinite_stream())

    client = httpx.AsyncClient(
        transport=_make_transport_handler(handler), base_url="http://127.0.0.1:11434"
    )
    provider = OllamaProvider(http_client=client)

    req = InferenceRequest(
        request_id="task-cancel-req",
        model_id="llama3.2:3b",
        messages=[ChatMessage(role=MessageRole.USER, content="test")],
    )

    async def run_inference():
        async for _ in provider.stream_inference(req):
            pass

    task = asyncio.create_task(run_inference())
    await asyncio.sleep(0.05)

    # Cancel task
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Confirm lease was freed
    tracker = await provider._get_or_create_tracker("llama3.2:3b")
    assert tracker.active_inferences == 0
    assert tracker.inferences_drained.is_set()


# ==============================================================================
# Live tests against local Ollama daemon (executed when daemon is online)
# ==============================================================================


async def _is_live_ollama_online() -> bool:
    """Check if local Ollama daemon is currently online."""
    try:
        async with httpx.AsyncClient(timeout=1.0) as c:
            resp = await c.get("http://127.0.0.1:11434/api/version")
            return resp.status_code == 200
    except Exception:
        return False


@pytest.mark.asyncio
async def test_live_ollama_real_inference_if_running():
    """Live integration test: execute real stream against installed Ollama model."""
    if not await _is_live_ollama_online():
        pytest.skip("Local Ollama daemon is not running")

    provider = OllamaProvider()
    try:
        models = await provider.discover_models()
        if not models:
            pytest.skip("No models installed in local Ollama")

        selected = models[0]
        req = InferenceRequest(
            model_id=selected.id,
            messages=[ChatMessage(role=MessageRole.USER, content="Count to 3.")],
            config=GenerationConfig(max_output_tokens=64),
        )

        tokens = []
        async for delta in provider.stream_inference(req):
            if delta.text:
                tokens.append(delta.text)
            if delta.finish_reason is not None:
                assert delta.finish_reason in (FinishReason.STOP, FinishReason.LENGTH)

        assert len(tokens) > 0
        full_text = "".join(tokens)
        assert len(full_text.strip()) > 0
    finally:
        await provider.close()
