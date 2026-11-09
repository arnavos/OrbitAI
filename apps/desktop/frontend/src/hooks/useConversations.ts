import { useMemo } from 'react';
import { useChat } from '../context/ChatContext';
import type { ConversationResponse } from '../types/conversation';

export function useConversations() {
  const {
    state,
    setActiveConversationId,
    setSearchQuery,
    createConversation,
    updateConversationTitle,
    deleteConversation,
    refreshConversations,
  } = useChat();

  const filteredConversations = useMemo(() => {
    if (!state.searchQuery.trim()) {
      return state.conversations;
    }
    const query = state.searchQuery.toLowerCase();
    return state.conversations.filter((c) =>
      c.title.toLowerCase().includes(query)
    );
  }, [state.conversations, state.searchQuery]);

  const activeConversation: ConversationResponse | null = useMemo(() => {
    if (!state.activeConversationId) return null;
    return state.conversations.find((c) => c.id === state.activeConversationId) || null;
  }, [state.conversations, state.activeConversationId]);

  return {
    conversations: state.conversations,
    filteredConversations,
    activeConversation,
    activeConversationId: state.activeConversationId,
    isLoading: state.isLoadingConversations,
    searchQuery: state.searchQuery,
    setSearchQuery,
    setActiveConversationId,
    createConversation,
    updateConversationTitle,
    deleteConversation,
    refreshConversations,
  };
}
