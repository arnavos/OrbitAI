import type { components } from './api';

export type ConversationResponse = components['schemas']['ConversationResponse'];
export type ConversationListResponse = components['schemas']['ConversationListResponse'];
export type MessageResponse = components['schemas']['MessageResponse'];
export type MessageListResponse = components['schemas']['MessageListResponse'];
export type ConversationCreate = components['schemas']['ConversationCreate'];
export type ConversationUpdate = components['schemas']['ConversationUpdate'];
export type SendMessageRequest = components['schemas']['SendMessageRequest'];
export type SendMessageResponse = components['schemas']['SendMessageResponse'];
export type CancelTurnResponse = components['schemas']['CancelTurnResponse'];
export type ModelDescriptorResponse = components['schemas']['ModelDescriptorResponse'];
export type ModelDiscoveryResponse = components['schemas']['ModelDiscoveryResponse'];

export interface ActiveStreamingState {
  isStreaming: boolean;
  conversationId: string | null;
  messageId: string | null;
  accumulatedText: string;
  tokenCount: number;
  allocatedContext: number | null;
  abortController: AbortController | null;
  startTime: number | null;
  error: string | null;
}
