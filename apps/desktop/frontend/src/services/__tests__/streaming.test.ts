import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { SSEStreamParser, StreamingConversationClient } from '../streaming';
import { BackendClient } from '../backend';
import type { StreamCallbacks, StreamEvent } from '../../types/streaming';

describe('SSEStreamParser — Framing, Boundaries & Event Protocol', () => {
  it('parses a complete streaming turn from started to completed', () => {
    const events: StreamEvent[] = [];
    const deltas: string[] = [];
    let completed = false;

    const callbacks: StreamCallbacks = {
      onEvent: (ev) => events.push(ev),
      onDelta: (text) => deltas.push(text),
      onCompleted: () => {
        completed = true;
      },
    };

    const parser = new SSEStreamParser(callbacks);

    const chunk1 =
      'event: stream.started\n' +
      'data: {"protocol_version":"1.0","event_type":"stream.started","conversation_id":"c1","message_id":"m1","event_seq":0,"payload":{"user_message":{"id":"u1","conversation_id":"c1","sequence":1,"role":"user","content":"hi","status":"completed","created_at":"2026-11-09T08:00:00Z"},"assistant_message_id":"m1","assistant_sequence":2,"model_id":"model","allocated_context":4096,"created_at":"2026-11-09T08:00:00Z"}}\n\n';

    const chunk2 =
      'event: message.delta\n' +
      'data: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":1,"payload":{"text":"Hello ","token_index":0,"accumulated_tokens":1}}\n\n' +
      'event: message.delta\n' +
      'data: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":2,"payload":{"text":"world!","token_index":1,"accumulated_tokens":2}}\n\n';

    const chunk3 =
      'event: stream.completed\n' +
      'data: {"protocol_version":"1.0","event_type":"stream.completed","conversation_id":"c1","message_id":"m1","event_seq":3,"payload":{"finish_reason":"stop","prompt_tokens":5,"completion_tokens":2,"total_tokens":7,"duration_ms":120.5}}\n\n';

    parser.feed(chunk1);
    parser.feed(chunk2);
    parser.feed(chunk3);
    parser.finalize();

    expect(events.length).toBe(4);
    expect(deltas).toEqual(['Hello ', 'world!']);
    expect(completed).toBe(true);
    expect(parser.hasReceivedTerminal()).toBe(true);
  });

  it('handles split SSE frames across multiple network chunks', () => {
    const deltas: string[] = [];
    const parser = new SSEStreamParser({
      onDelta: (text) => deltas.push(text),
    });

    const fullMessage =
      'event: message.delta\n' +
      'data: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":1,"payload":{"text":"Split token across chunks","token_index":0,"accumulated_tokens":1}}\n\n';

    // Split across 3 arbitrary chunk slices
    const slice1 = fullMessage.slice(0, 15);
    const slice2 = fullMessage.slice(15, 60);
    const slice3 = fullMessage.slice(60);

    parser.feed(slice1);
    expect(deltas.length).toBe(0); // Incomplete frame

    parser.feed(slice2);
    expect(deltas.length).toBe(0); // Still incomplete

    parser.feed(slice3);
    expect(deltas.length).toBe(1); // Completed
    expect(deltas[0]).toBe('Split token across chunks');
  });

  it('handles CRLF line endings (Windows/HTTP standard)', () => {
    const deltas: string[] = [];
    const parser = new SSEStreamParser({
      onDelta: (text) => deltas.push(text),
    });

    const crlfChunk =
      'event: message.delta\r\n' +
      'data: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":1,"payload":{"text":"CRLF ok","token_index":0,"accumulated_tokens":1}}\r\n\r\n';

    parser.feed(crlfChunk);
    expect(deltas).toEqual(['CRLF ok']);
  });

  it('handles multiline SSE data fields correctly', () => {
    let capturedEvent: StreamEvent | null = null;
    const parser = new SSEStreamParser({
      onEvent: (ev) => {
        capturedEvent = ev;
      },
    });

    // In SSE, multiple data lines in the same block are joined with newline
    const multilineBlock =
      'event: message.delta\n' +
      'data: {"protocol_version":"1.0",\n' +
      'data: "event_type":"message.delta",\n' +
      'data: "conversation_id":"c1","message_id":"m1","event_seq":1,\n' +
      'data: "payload":{"text":"multiline payload","token_index":0,"accumulated_tokens":1}}\n\n';

    parser.feed(multilineBlock);
    expect(capturedEvent).not.toBeNull();
    expect((capturedEvent as unknown as { payload: { text: string } }).payload.text).toBe(
      'multiline payload'
    );
  });

  it('safely preserves Unicode and emoji characters without corruption', () => {
    const deltas: string[] = [];
    const parser = new SSEStreamParser({
      onDelta: (text) => deltas.push(text),
    });

    const unicodeChunk =
      'event: message.delta\n' +
      'data: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":1,"payload":{"text":"OrbitAI 🚀 ✨ 宇宙","token_index":0,"accumulated_tokens":1}}\n\n';

    parser.feed(unicodeChunk);
    expect(deltas[0]).toBe('OrbitAI 🚀 ✨ 宇宙');
  });

  it('ignores SSE heartbeat comments (: ping)', () => {
    const events: StreamEvent[] = [];
    const parser = new SSEStreamParser({
      onEvent: (ev) => events.push(ev),
    });

    parser.feed(': ping\n\n');
    parser.feed(': keepalive comment\n\n');
    expect(events.length).toBe(0);
  });

  it('handles malformed JSON safely without throwing and triggers onError', () => {
    let caughtError: Error | null = null;
    const parser = new SSEStreamParser({
      onError: (err) => {
        caughtError = err;
      },
    });

    parser.feed('event: message.delta\ndata: {this is not json}\n\n');
    expect(caughtError).not.toBeNull();
    expect((caughtError as unknown as Error)?.message).toContain(
      'Failed to parse SSE JSON payload'
    );
  });


  it('handles unknown event types safely and logs warning without throwing', () => {
    const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const events: StreamEvent[] = [];
    const parser = new SSEStreamParser({
      onEvent: (ev) => events.push(ev),
    });

    parser.feed(
      'event: stream.future_extension\ndata: {"protocol_version":"1.0","event_type":"stream.future_extension","conversation_id":"c1","message_id":"m1","event_seq":1,"payload":{}}\n\n'
    );

    expect(events.length).toBe(1);
    expect(consoleSpy).toHaveBeenCalledWith(
      expect.stringContaining('Unknown or unhandled SSE event type')
    );
    consoleSpy.mockRestore();
  });

  it('detects out of order sequence numbers and logs warning', () => {
    const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const parser = new SSEStreamParser({});

    parser.feed(
      'event: message.delta\ndata: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":5,"payload":{"text":"a","token_index":0,"accumulated_tokens":1}}\n\n'
    );

    parser.feed(
      'event: message.delta\ndata: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":3,"payload":{"text":"b","token_index":1,"accumulated_tokens":2}}\n\n'
    );

    expect(consoleSpy).toHaveBeenCalledWith(
      expect.stringContaining('Out of order or duplicate event sequence')
    );
    consoleSpy.mockRestore();
  });

  it('handles stream.cancelled terminal event', () => {
    let cancelledReason = '';
    const parser = new SSEStreamParser({
      onCancelled: (payload) => {
        cancelledReason = payload.reason;
      },
    });

    parser.feed(
      'event: stream.cancelled\ndata: {"protocol_version":"1.0","event_type":"stream.cancelled","conversation_id":"c1","message_id":"m1","event_seq":2,"payload":{"reason":"cancelled_by_user","partial_tokens_count":3,"partial_text_length":15}}\n\n'
    );

    expect(cancelledReason).toBe('cancelled_by_user');
    expect(parser.hasReceivedTerminal()).toBe(true);
  });

  it('handles stream.failed terminal event', () => {
    let errorCode = '';
    const parser = new SSEStreamParser({
      onFailed: (payload) => {
        errorCode = payload.error_code;
      },
    });

    parser.feed(
      'event: stream.failed\ndata: {"protocol_version":"1.0","event_type":"stream.failed","conversation_id":"c1","message_id":"m1","event_seq":1,"payload":{"error_code":"INFERENCE_FAILED","error_message":"GPU OOM","details":null}}\n\n'
    );

    expect(errorCode).toBe('INFERENCE_FAILED');
    expect(parser.hasReceivedTerminal()).toBe(true);
  });

  it('ignores content deltas arriving after terminal event', () => {
    const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const deltas: string[] = [];
    const parser = new SSEStreamParser({
      onDelta: (text) => deltas.push(text),
    });

    parser.feed(
      'event: stream.completed\ndata: {"protocol_version":"1.0","event_type":"stream.completed","conversation_id":"c1","message_id":"m1","event_seq":1,"payload":{"finish_reason":"stop","prompt_tokens":1,"completion_tokens":1,"total_tokens":2,"duration_ms":10}}\n\n'
    );

    parser.feed(
      'event: message.delta\ndata: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":2,"payload":{"text":"illegal delta","token_index":0,"accumulated_tokens":1}}\n\n'
    );

    expect(deltas.length).toBe(0);
    expect(consoleSpy).toHaveBeenCalledWith(
      expect.stringContaining('Received message.delta after terminal event')
    );
    consoleSpy.mockRestore();
  });

});

describe('StreamingConversationClient — Network & Lifecycle Integration', () => {
  let backendClient: BackendClient;
  let client: StreamingConversationClient;
  const originalFetch = global.fetch;

  beforeEach(() => {
    backendClient = new BackendClient();
    (
      backendClient as unknown as {
        connection: { apiUrl: string; wsUrl: string; sessionToken: string };
      }
    ).connection = {
      apiUrl: 'http://127.0.0.1:49999',
      wsUrl: 'ws://127.0.0.1:49999/ws',
      sessionToken: 'test-session-token-123',
    };
    client = new StreamingConversationClient(backendClient);
  });

  afterEach(() => {
    global.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('submits streaming message with correct headers and parses stream', async () => {
    const sseBody =
      'event: stream.started\ndata: {"protocol_version":"1.0","event_type":"stream.started","conversation_id":"c1","message_id":"m1","event_seq":0,"payload":{"user_message":{"id":"u1","conversation_id":"c1","sequence":1,"role":"user","content":"hi","status":"completed","created_at":"2026-11-09T08:00:00Z"},"assistant_message_id":"m1","assistant_sequence":2,"model_id":"m","allocated_context":4096,"created_at":"2026-11-09T08:00:00Z"}}\n\n' +
      'event: message.delta\ndata: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":1,"payload":{"text":"Streamed response","token_index":0,"accumulated_tokens":1}}\n\n' +
      'event: stream.completed\ndata: {"protocol_version":"1.0","event_type":"stream.completed","conversation_id":"c1","message_id":"m1","event_seq":2,"payload":{"finish_reason":"stop","prompt_tokens":2,"completion_tokens":1,"total_tokens":3,"duration_ms":50}}\n\n';

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseBody));
        controller.close();
      },
    });

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      body: stream,
    } as unknown as Response);

    const deltas: string[] = [];
    let completed = false;

    const controller = await client.streamMessage('conv-uuid-1', 'hi', {
      onDelta: (text) => deltas.push(text),
      onCompleted: () => {
        completed = true;
      },
    });

    expect(controller).toBeInstanceOf(AbortController);
    expect(global.fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:49999/api/v1/conversations/conv-uuid-1/messages/stream',
      expect.objectContaining({
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-orbit-session-token': 'test-session-token-123',
        },
        body: JSON.stringify({ content: 'hi' }),
      })
    );

    // Wait for async stream reader loop to finish
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(deltas).toEqual(['Streamed response']);
    expect(completed).toBe(true);
  });

  it('rejects with error on non-2xx HTTP responses before stream reading', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      text: async () => JSON.stringify({ error: { message: "Conversation 'xyz' not found" } }),
    } as unknown as Response);

    let errorFired = false;
    await expect(
      client.streamMessage('xyz', 'prompt', {
        onError: () => {
          errorFired = true;
        },
      })
    ).rejects.toThrow("Conversation 'xyz' not found");

    expect(errorFired).toBe(true);
  });

  it('notifies onError when stream terminates abruptly without a terminal event', async () => {
    const incompleteBody =
      'event: message.delta\ndata: {"protocol_version":"1.0","event_type":"message.delta","conversation_id":"c1","message_id":"m1","event_seq":1,"payload":{"text":"Incomplete...","token_index":0,"accumulated_tokens":1}}\n\n';

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(incompleteBody));
        controller.close(); // Closed without stream.completed
      },
    });

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      body: stream,
    } as unknown as Response);

    let errorFired: Error | null = null;
    await client.streamMessage('c1', 'prompt', {
      onError: (err) => {
        errorFired = err;
      },
    });

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(errorFired).not.toBeNull();
    expect((errorFired as unknown as Error)?.message).toContain(
      'closed unexpectedly without a terminal event'
    );
  });


  it('calls cancelTurn REST endpoint and parses response', async () => {
    const mockCancelResponse = {
      success: true,
      data: {
        cancelled: true,
        message_id: 'm1-cancelled',
        detail: 'Inference cancellation initiated',
      },
      correlation_id: 'corr-1',
    };

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => mockCancelResponse,
    } as unknown as Response);

    const result = await client.cancelTurn('c1');
    expect(result.cancelled).toBe(true);
    expect(result.message_id).toBe('m1-cancelled');
    expect(global.fetch).toHaveBeenCalledWith(
      'http://127.0.0.1:49999/api/v1/conversations/c1/cancel',
      expect.objectContaining({
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-orbit-session-token': 'test-session-token-123',
        },
      })
    );
  });
});
