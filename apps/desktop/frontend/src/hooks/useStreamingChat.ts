import { useMemo } from 'react';
import { useChat } from '../context/ChatContext';
import type { MessageResponse } from '../types/conversation';

export function useStreamingChat() {
  const {
    state,
    sendStreamingMessage,
    cancelStreaming,
    setDraft,
  } = useChat();

  const activeMessages: MessageResponse[] = useMemo(() => {
    if (!state.activeConversationId) return [];
    return state.messagesByConversation[state.activeConversationId] || [];
  }, [state.activeConversationId, state.messagesByConversation]);

  const activeDraft = useMemo(() => {
    const key = state.activeConversationId || 'new';
    return state.drafts[key] || '';
  }, [state.activeConversationId, state.drafts]);

  const updateDraft = (text: string) => {
    const key = state.activeConversationId || 'new';
    setDraft(key, text);
  };

  return {
    messages: activeMessages,
    isLoadingMessages: state.isLoadingMessages,
    streamingState: state.streamingState,
    isStreaming: state.streamingState.isStreaming,
    draft: activeDraft,
    updateDraft,
    sendMessage: sendStreamingMessage,
    cancelStreaming,
  };
}
