/**
 * Strongly typed contracts and event envelopes for Server-Sent Events (SSE) token streaming.
 */

export const STREAM_PROTOCOL_VERSION = '1.0';

export type StreamEventType =
  | 'stream.started'
  | 'message.delta'
  | 'stream.completed'
  | 'stream.failed'
  | 'stream.interrupted'
  | 'stream.cancelled';

export interface StreamUserMessageSnapshot {
  id: string;
  conversation_id: string;
  sequence: number;
  role: 'user';
  content: string;
  status: string;
  created_at: string;
}

export interface StreamStartedPayload {
  user_message: StreamUserMessageSnapshot;
  assistant_message_id: string;
  assistant_sequence: number;
  model_id: string;
  allocated_context: number;
  created_at: string;
}

export interface MessageDeltaPayload {
  text: string;
  token_index: number;
  accumulated_tokens: number;
}

export interface StreamCompletedPayload {
  finish_reason: string;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  duration_ms: number;
}

export interface StreamInterruptedPayload {
  reason: string;
  partial_tokens_count: number;
  partial_text_length: number;
}

export interface StreamCancelledPayload {
  reason: string;
  partial_tokens_count: number;
  partial_text_length: number;
}

export interface StreamFailedPayload {
  error_code: string;
  error_message: string;
  details: Record<string, unknown> | null;
}

export interface StreamEventEnvelope<T> {
  protocol_version: string;
  event_type: StreamEventType;
  conversation_id: string;
  message_id: string;
  event_seq: number;
  payload: T;
}

export type StreamStartedEvent = StreamEventEnvelope<StreamStartedPayload> & {
  event_type: 'stream.started';
};

export type MessageDeltaEvent = StreamEventEnvelope<MessageDeltaPayload> & {
  event_type: 'message.delta';
};

export type StreamCompletedEvent = StreamEventEnvelope<StreamCompletedPayload> & {
  event_type: 'stream.completed';
};

export type StreamInterruptedEvent = StreamEventEnvelope<StreamInterruptedPayload> & {
  event_type: 'stream.interrupted';
};

export type StreamCancelledEvent = StreamEventEnvelope<StreamCancelledPayload> & {
  event_type: 'stream.cancelled';
};

export type StreamFailedEvent = StreamEventEnvelope<StreamFailedPayload> & {
  event_type: 'stream.failed';
};

export type StreamEvent =
  | StreamStartedEvent
  | MessageDeltaEvent
  | StreamCompletedEvent
  | StreamInterruptedEvent
  | StreamCancelledEvent
  | StreamFailedEvent;

export interface StreamCallbacks {
  onEvent?: (event: StreamEvent) => void;
  onStarted?: (payload: StreamStartedPayload, envelope: StreamStartedEvent) => void;
  onDelta?: (deltaText: string, payload: MessageDeltaPayload, envelope: MessageDeltaEvent) => void;
  onCompleted?: (payload: StreamCompletedPayload, envelope: StreamCompletedEvent) => void;
  onInterrupted?: (payload: StreamInterruptedPayload, envelope: StreamInterruptedEvent) => void;
  onCancelled?: (payload: StreamCancelledPayload, envelope: StreamCancelledEvent) => void;
  onFailed?: (payload: StreamFailedPayload, envelope: StreamFailedEvent) => void;
  onError?: (error: Error) => void;
}
