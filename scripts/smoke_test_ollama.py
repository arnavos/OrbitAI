#!/usr/bin/env python3
"""Developer smoke test verifying real local Ollama inference through ORBIT runtime.

Usage:
    uv run --project backend python scripts/smoke_test_ollama.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Ensure backend root is on sys.path
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Set test environment to avoid requiring parent watchdog
os.environ.setdefault("APP_ENV", "development")

from app.ai.exceptions import AIError
from app.ai.providers.ollama import OllamaProvider
from app.ai.registry import ModelRegistry
from app.ai.types import (
    ChatMessage,
    FinishReason,
    GenerationConfig,
    InferenceRequest,
    MessageRole,
    ProviderStatus,
)


async def run_smoke_test() -> int:
    """Execute end-to-end smoke test against the local Ollama provider."""
    print("=" * 70)
    print(" ORBIT AI RUNTIME — LOCAL OLLAMA INFERENCE SMOKE TEST")
    print("=" * 70)

    registry = ModelRegistry()
    provider = OllamaProvider()

    try:
        # 1. Register provider in registry
        print("\n[1/6] Initializing AI Model Registry and registering OllamaProvider...")
        await registry.register_provider(provider)
        print(f"✓ Registered provider: '{provider.provider_id}' ({provider.provider_type})")

        # 2. Probe provider health
        print("\n[2/6] Probing local Ollama daemon connectivity...")
        health = await provider.check_health()
        print(f"  Status:    {health.status.value.upper()}")
        print(f"  Latency:   {health.latency_ms:.1f} ms" if health.latency_ms else "  Latency:   N/A")
        print(f"  Message:   {health.message}")

        if health.status != ProviderStatus.ONLINE:
            print("\n❌ SMOKE TEST FAILED: Ollama daemon is offline or unreachable.")
            print(f"   Diagnostic: {health.message}")
            print("   Please ensure Ollama is running: 'ollama serve'")
            return 1

        # 3. Discover available models through registry
        print("\n[3/6] Discovering installed models through ModelRegistry...")
        discovery = await registry.discover_models()
        if discovery.errors:
            print(f"  Discovery warnings/errors: {discovery.errors}")

        if not discovery.models:
            print("\n❌ SMOKE TEST FAILED: Ollama is running but no models are installed.")
            print("   Please pull a model: 'ollama pull llama3.2:3b'")
            return 1

        print(f"✓ Discovered {len(discovery.models)} model(s):")
        for idx, m in enumerate(discovery.models, 1):
            ctx = f"{m.context_window_capacity} tokens" if m.context_window_capacity else "unknown"
            print(f"  [{idx}] {m.id} (family: {m.model_family}, ctx: {ctx}, status: {m.availability_status})")

        # 4. Select target model
        target_model = None
        for preferred in ("ollama/llama3.2:3b", "ollama/qwen3:4b"):
            for m in discovery.models:
                if m.id == preferred:
                    target_model = m
                    break
            if target_model:
                break

        if not target_model:
            target_model = discovery.models[0]

        print(f"\n[4/6] Selected target model: '{target_model.id}'")
        print(f"  Native ID:         {target_model.native_model_id}")
        print(f"  Verified context:  {target_model.context_window_capacity or 'unspecified'}")
        print(f"  Quantization:      {target_model.quantization or 'unspecified'}")

        # 5. Build and submit inference request
        prompt_text = "In exactly two clear sentences, explain why local-first AI is important for user privacy."
        print(f"\n[5/6] Submitting real inference prompt:\n  \"{prompt_text}\"\n")
        print("--- Streamed Output Begin ---")

        messages = [
            ChatMessage(
                role=MessageRole.SYSTEM,
                content="You are ORBIT, a helpful local personal intelligence assistant. Be concise.",
            ),
            ChatMessage(
                role=MessageRole.USER,
                content=prompt_text,
            ),
        ]
        request = InferenceRequest(
            model_id=target_model.id,
            messages=messages,
            config=GenerationConfig(
                temperature=0.7,
                max_output_tokens=256,
            ),
        )

        stream = provider.stream_inference(request)

        collected_text: list[str] = []
        token_count: int = 0
        first_token_time: float | None = None
        start_time = time.monotonic()
        final_delta = None

        async for delta in stream:
            if delta.text:
                if first_token_time is None:
                    first_token_time = time.monotonic()
                sys.stdout.write(delta.text)
                sys.stdout.flush()
                collected_text.append(delta.text)
                token_count += 1
            if delta.finish_reason is not None:
                final_delta = delta

        total_time = time.monotonic() - start_time
        ttft = (first_token_time - start_time) if first_token_time else total_time
        full_response = "".join(collected_text).strip()

        print("\n--- Streamed Output End ---")

        # 6. Verify non-empty response and accounting
        print("\n[6/6] Validating inference completion...")
        if not full_response:
            print("❌ SMOKE TEST FAILED: Stream completed but generated response was empty.")
            return 1

        finish_reason = final_delta.finish_reason if final_delta else None
        usage = final_delta.usage if final_delta else None

        print(f"✓ Generated characters:  {len(full_response)}")
        print(f"✓ Finish reason:         {finish_reason.value if finish_reason else 'None'}")
        if usage:
            print(f"✓ Prompt tokens:         {usage.prompt_tokens}")
            print(f"✓ Completion tokens:     {usage.completion_tokens}")
            print(f"✓ Total tokens:          {usage.total_tokens} (source: {usage.source})")
        print(f"✓ Time to first token:   {ttft * 1000.0:.1f} ms")
        print(f"✓ Total generation time: {total_time:.2f} s")
        if usage and usage.completion_tokens and total_time > 0:
            speed = usage.completion_tokens / total_time
            print(f"✓ Generation throughput: {speed:.1f} tokens/s")

        if finish_reason not in (FinishReason.STOP, FinishReason.LENGTH):
            print(f"❌ SMOKE TEST FAILED: Unexpected terminal finish reason '{finish_reason}'")
            return 1

        print("\n" + "=" * 70)
        print("✓ ORBIT LOCAL OLLAMA INFERENCE SMOKE TEST COMPLETED SUCCESSFULLY!")
        print("=" * 70)
        return 0

    except AIError as err:
        print(f"\n❌ SMOKE TEST FAILED with AIError: {err.code} - {err.message}")
        if err.details:
            print(f"   Details: {err.details}")
        return 1
    except Exception as err:
        print(f"\n❌ SMOKE TEST FAILED with unexpected exception: {type(err).__name__}: {err}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        await provider.close()


if __name__ == "__main__":
    exit_code = asyncio.run(run_smoke_test())
    sys.exit(exit_code)

