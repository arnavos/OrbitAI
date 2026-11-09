import { backendClient } from './backend';
import type {
  ConversationResponse,
  ConversationListResponse,
  MessageListResponse,
  ConversationCreate,
  ConversationUpdate,
} from '../types/conversation';
import type { APIEnvelope } from '../types/contracts';

export class ConversationService {
  constructor(private client = backendClient) {}

  async listConversations(limit = 50, offset = 0): Promise<ConversationListResponse> {
    const res = await this.client.fetch<APIEnvelope<ConversationListResponse>>(
      `/api/v1/conversations?limit=${limit}&offset=${offset}`
    );
    if (res.data) {
      return res.data;
    }
    return { conversations: [], total: 0 };
  }

  async createConversation(payload: ConversationCreate = {}): Promise<ConversationResponse> {
    const res = await this.client.fetch<APIEnvelope<ConversationResponse>>('/api/v1/conversations', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    if (res.data) {
      return res.data;
    }
    throw new Error('Failed to create conversation: invalid response');
  }

  async getConversation(conversationId: string): Promise<ConversationResponse> {
    const res = await this.client.fetch<APIEnvelope<ConversationResponse>>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}`
    );
    if (res.data) {
      return res.data;
    }
    throw new Error(`Conversation not found: ${conversationId}`);
  }

  async updateConversation(
    conversationId: string,
    payload: ConversationUpdate
  ): Promise<ConversationResponse> {
    const res = await this.client.fetch<APIEnvelope<ConversationResponse>>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      }
    );
    if (res.data) {
      return res.data;
    }
    throw new Error(`Failed to update conversation: ${conversationId}`);
  }

  async deleteConversation(conversationId: string): Promise<boolean> {
    const res = await this.client.fetch<APIEnvelope<boolean>>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}`,
      {
        method: 'DELETE',
      }
    );
    return Boolean(res.data);
  }

  async listMessages(
    conversationId: string,
    limit = 100,
    beforeSequence?: number
  ): Promise<MessageListResponse> {
    let url = `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages?limit=${limit}`;
    if (beforeSequence !== undefined && beforeSequence !== null) {
      url += `&before_sequence=${beforeSequence}`;
    }
    const res = await this.client.fetch<APIEnvelope<MessageListResponse>>(url);
    if (res.data) {
      return res.data;
    }
    return { messages: [], total: 0 };
  }
}

export const conversationService = new ConversationService();
