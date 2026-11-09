/**
 * Streaming transport client and robust SSE frame parser for real-time inference delivery.
 */

import { BackendClient } from './backend';
import {
  STREAM_PROTOCOL_VERSION,
  type MessageDeltaEvent,
  type MessageDeltaPayload,
  type StreamCallbacks,
  type StreamCancelledEvent,
  type StreamCancelledPayload,
  type StreamCompletedEvent,
  type StreamCompletedPayload,
  type StreamEvent,
  type StreamEventType,
  type StreamFailedEvent,
  type StreamFailedPayload,
  type StreamInterruptedEvent,
  type StreamInterruptedPayload,
  type StreamStartedEvent,
} from '../types/streaming';


export interface CancelTurnResponse {
  cancelled: boolean;
  message_id: string | null;
  detail: string;
}

/**
 * Incrementally parses SSE frames across arbitrary chunk boundaries, supporting CRLF/LF,
 * multiline data fields, comments, sequence order validation, and typed event dispatch.
 */
export class SSEStreamParser {
  private buffer = '';
  private lastSequence = -1;
  private terminalReceived = false;

  constructor(private callbacks: StreamCallbacks) {}

  /**
   * Whether a valid terminal event (completed, interrupted, cancelled, or failed) was parsed.
   */
  hasReceivedTerminal(): boolean {
    return this.terminalReceived;
  }

  /**
   * Push an incoming string chunk into the parser and dispatch any complete events.
   */
  feed(chunk: string): void {
    // Normalize CRLF to LF for consistent line processing
    this.buffer += chunk.replace(/\r\n/g, '\n');

    // Split on double-newline (event boundary in SSE)
    const blocks = this.buffer.split('\n\n');
    // Retain trailing un-delimited block in buffer
    this.buffer = blocks.pop() || '';

    for (const block of blocks) {
      if (!block.trim()) continue;
      this.processBlock(block);
    }
  }

  /**
   * Finalize parsing when the stream ends. Flushes any remaining completed block.
   */
  finalize(): void {
    if (this.buffer.trim()) {
      this.processBlock(this.buffer);
      this.buffer = '';
    }
  }

  private processBlock(block: string): void {
    const lines = block.split('\n');
    let eventName: StreamEventType | null = null;
    const dataLines: string[] = [];

    for (const rawLine of lines) {
      const line = rawLine.trimEnd();
      if (!line || line.startsWith(':')) {
        // Empty line or SSE comment (e.g. heartbeat ping)
        continue;
      }

      if (line.startsWith('event:')) {
        eventName = line.slice(6).trim() as StreamEventType;
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trimStart());
      }
    }

    if (dataLines.length === 0) {
      return;
    }

    const rawData = dataLines.join('\n');
    let envelope: StreamEvent;
    try {
      envelope = JSON.parse(rawData);
    } catch (parseError) {
      const err = new Error(
        `Failed to parse SSE JSON payload: ${parseError instanceof Error ? parseError.message : String(parseError)}`
      );
      this.callbacks.onError?.(err);
      return;
    }

    if (eventName && (!envelope || !envelope.event_type)) {
      (envelope as unknown as { event_type: StreamEventType }).event_type = eventName;
    }

    if (!envelope || typeof envelope !== 'object' || !envelope.event_type) {
      const err = new Error('Invalid SSE event envelope: missing event_type');
      this.callbacks.onError?.(err);
      return;
    }


    // Protocol version validation
    if (envelope.protocol_version && envelope.protocol_version !== STREAM_PROTOCOL_VERSION) {
      console.warn(
        `SSE Protocol version mismatch: received '${envelope.protocol_version}', expected '${STREAM_PROTOCOL_VERSION}'`
      );
    }

    // Sequence ordering validation
    if (typeof envelope.event_seq === 'number') {
      if (this.lastSequence !== -1 && envelope.event_seq <= this.lastSequence) {
        console.warn(
          `Out of order or duplicate event sequence: received ${envelope.event_seq}, last was ${this.lastSequence}`
        );
      }
      this.lastSequence = envelope.event_seq;
    }

    this.dispatch(envelope);
  }

  private dispatch(envelope: StreamEvent): void {
    this.callbacks.onEvent?.(envelope);

    switch (envelope.event_type) {
      case 'stream.started': {
        const ev = envelope as StreamStartedEvent;
        this.callbacks.onStarted?.(ev.payload, ev);
        break;
      }
      case 'message.delta': {
        if (this.terminalReceived) {
          console.warn('Received message.delta after terminal event had already arrived');
          return;
        }
        const ev = envelope as MessageDeltaEvent;
        const deltaText = ev.payload?.text ?? '';
        this.callbacks.onDelta?.(deltaText, ev.payload as MessageDeltaPayload, ev);
        break;
      }
      case 'stream.completed': {
        this.terminalReceived = true;
        const ev = envelope as StreamCompletedEvent;
        this.callbacks.onCompleted?.(ev.payload as StreamCompletedPayload, ev);
        break;
      }
      case 'stream.interrupted': {
        this.terminalReceived = true;
        const ev = envelope as StreamInterruptedEvent;
        this.callbacks.onInterrupted?.(ev.payload as StreamInterruptedPayload, ev);
        break;
      }
      case 'stream.cancelled': {
        this.terminalReceived = true;
        const ev = envelope as StreamCancelledEvent;
        this.callbacks.onCancelled?.(ev.payload as StreamCancelledPayload, ev);
        break;
      }
      case 'stream.failed': {
        this.terminalReceived = true;
        const ev = envelope as StreamFailedEvent;
        this.callbacks.onFailed?.(ev.payload as StreamFailedPayload, ev);
        break;
      }
      default:
        console.warn(`Unknown or unhandled SSE event type: ${(envelope as StreamEvent).event_type}`);
    }
  }
}

/**
 * Reusable typed streaming conversation client.
 */
export class StreamingConversationClient {
  constructor(private backendClient: BackendClient) {}

  /**
   * Submit a user prompt to a conversation thread and stream model response via SSE.
   * Returns the active AbortController allowing immediate user cancellation.
   */
  async streamMessage(
    conversationId: string,
    content: string,
    callbacks: StreamCallbacks,
    options?: {
      modelId?: string;
      temperature?: number;
      maxOutputTokens?: number;
      systemPrompt?: string;
      abortController?: AbortController;
    }
  ): Promise<AbortController> {
    const conn = await this.backendClient.getConnection();
    if (!conn) {
      throw new Error('Backend connection parameters unavailable');
    }

    const controller = options?.abortController || new AbortController();
    const url = `${conn.apiUrl}/api/v1/conversations/${encodeURIComponent(conversationId)}/messages/stream`;

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-orbit-session-token': conn.sessionToken,
      },
      body: JSON.stringify({
        content,
        model_id: options?.modelId,
        temperature: options?.temperature,
        max_output_tokens: options?.maxOutputTokens,
        system_prompt: options?.systemPrompt,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const errorText = await response.text();
      let errorDetail: string;
      try {
        const errorJson = JSON.parse(errorText);
        errorDetail = errorJson?.error?.message || errorJson?.detail || errorText;
      } catch {
        errorDetail = errorText || response.statusText;
      }
      const err = new Error(`Streaming request failed (HTTP ${response.status}): ${errorDetail}`);
      callbacks.onError?.(err);
      throw err;
    }

    if (!response.body) {
      const err = new Error('HTTP response body is empty; streaming impossible');
      callbacks.onError?.(err);
      throw err;
    }

    // Process stream asynchronously without blocking caller return
    this.consumeStream(response.body, callbacks, controller).catch((err) => {
      if (err instanceof Error && err.name === 'AbortError') {
        // Normal client-side abort
        return;
      }
      callbacks.onError?.(err instanceof Error ? err : new Error(String(err)));
    });

    return controller;
  }

  /**
   * Explicitly cancel an active in-flight inference generation on a conversation.
   */
  async cancelTurn(conversationId: string): Promise<CancelTurnResponse> {
    const conn = await this.backendClient.getConnection();
    if (!conn) {
      throw new Error('Backend connection parameters unavailable');
    }

    const url = `${conn.apiUrl}/api/v1/conversations/${encodeURIComponent(conversationId)}/cancel`;
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-orbit-session-token': conn.sessionToken,
      },
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`Cancellation request failed (HTTP ${response.status}): ${errorText}`);
    }

    const envelope = await response.json();
    return envelope.data as CancelTurnResponse;
  }

  private async consumeStream(
    body: ReadableStream<Uint8Array>,
    callbacks: StreamCallbacks,
    controller: AbortController
  ): Promise<void> {
    const reader = body.getReader();
    const decoder = new TextDecoder('utf-8');
    const parser = new SSEStreamParser(callbacks);

    try {
      while (true) {
        if (controller.signal.aborted) {
          break;
        }

        const { done, value } = await reader.read();
        if (done) {
          break;
        }

        if (value) {
          const chunk = decoder.decode(value, { stream: true });
          parser.feed(chunk);
        }
      }

      // Finalize any remaining trailing buffer
      parser.finalize();

      if (!parser.hasReceivedTerminal() && !controller.signal.aborted) {
        const incompleteErr = new Error('SSE stream closed unexpectedly without a terminal event');
        callbacks.onError?.(incompleteErr);
      }
    } finally {
      reader.releaseLock();
    }
  }
}
