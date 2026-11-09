import React, { createContext, useContext, useEffect, useReducer, useRef, useCallback } from 'react';
import { conversationService } from '../services/conversation';
import { modelService } from '../services/models';
import { StreamingConversationClient } from '../services/streaming';
import { backendClient } from '../services/backend';
import type {
  ConversationResponse,
  MessageResponse,
  ModelDescriptorResponse,
  ActiveStreamingState,
} from '../types/conversation';
import type { StreamCallbacks } from '../types/streaming';

interface ChatState {
  conversations: ConversationResponse[];
  activeConversationId: string | null;
  messagesByConversation: Record<string, MessageResponse[]>;
  isLoadingConversations: boolean;
  isLoadingMessages: boolean;
  searchQuery: string;
  sidebarOpen: boolean;
  activeView: 'chat' | 'system';
  availableModels: ModelDescriptorResponse[];
  selectedModelId: string;
  isLoadingModels: boolean;
  modelError: string | null;
  drafts: Record<string, string>;
  streamingState: ActiveStreamingState;
  isSubmitting: boolean;
  error: string | null;
}

type ChatAction =
  | { type: 'SET_ACTIVE_VIEW'; payload: 'chat' | 'system' }
  | { type: 'TOGGLE_SIDEBAR' }
  | { type: 'SET_SIDEBAR_OPEN'; payload: boolean }
  | { type: 'SET_SEARCH_QUERY'; payload: string }
  | { type: 'SET_SELECTED_MODEL'; payload: string }
  | { type: 'SET_DRAFT'; payload: { conversationId: string; text: string } }
  | { type: 'SET_SUBMITTING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: string | null }
  | { type: 'SET_LOADING_CONVERSATIONS'; payload: boolean }
  | { type: 'SET_CONVERSATIONS'; payload: ConversationResponse[] }
  | { type: 'ADD_CONVERSATION'; payload: ConversationResponse }
  | { type: 'UPDATE_CONVERSATION'; payload: { id: string; title: string; model_id?: string | null } }
  | { type: 'REMOVE_CONVERSATION'; payload: string }
  | { type: 'SET_ACTIVE_CONVERSATION'; payload: string | null }
  | { type: 'SET_LOADING_MESSAGES'; payload: boolean }
  | { type: 'SET_MESSAGES'; payload: { conversationId: string; messages: MessageResponse[] } }
  | { type: 'APPEND_MESSAGE'; payload: { conversationId: string; message: MessageResponse } }
  | {
      type: 'UPDATE_MESSAGE';
      payload: { conversationId: string; messageId: string; updates: Partial<MessageResponse> };
    }
  | { type: 'SET_LOADING_MODELS'; payload: boolean }
  | { type: 'SET_MODELS'; payload: { models: ModelDescriptorResponse[]; error?: string | null } }
  | {
      type: 'START_STREAMING';
      payload: {
        conversationId: string;
        assistantMessageId: string;
        abortController: AbortController;
      };
    }
  | {
      type: 'STREAM_STARTED';
      payload: {
        conversationId: string;
        assistantMessageId: string;
        allocatedContext: number | null;
      };
    }
  | {
      type: 'APPEND_STREAM_DELTA';
      payload: {
        conversationId: string;
        deltaText: string;
        tokenCount: number;
      };
    }
  | {
      type: 'FINISH_STREAM';
      payload: {
        conversationId: string;
        status: 'completed' | 'interrupted' | 'failed';
        finishReason?: string;
        promptTokens?: number | null;
        completionTokens?: number | null;
        error?: string | null;
      };
    };

const initialStreamingState: ActiveStreamingState = {
  isStreaming: false,
  conversationId: null,
  messageId: null,
  accumulatedText: '',
  tokenCount: 0,
  allocatedContext: null,
  abortController: null,
  startTime: null,
  error: null,
};

const initialState: ChatState = {
  conversations: [],
  activeConversationId: null,
  messagesByConversation: {},
  isLoadingConversations: false,
  isLoadingMessages: false,
  searchQuery: '',
  sidebarOpen: true,
  activeView: 'chat',
  availableModels: [],
  selectedModelId: 'ollama/llama3.2:3b',
  isLoadingModels: false,
  modelError: null,
  drafts: {},
  streamingState: initialStreamingState,
  isSubmitting: false,
  error: null,
};

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'SET_ACTIVE_VIEW':
      return { ...state, activeView: action.payload };

    case 'TOGGLE_SIDEBAR':
      return { ...state, sidebarOpen: !state.sidebarOpen };

    case 'SET_SIDEBAR_OPEN':
      return { ...state, sidebarOpen: action.payload };

    case 'SET_SEARCH_QUERY':
      return { ...state, searchQuery: action.payload };

    case 'SET_SELECTED_MODEL':
      return { ...state, selectedModelId: action.payload };

    case 'SET_DRAFT':
      return {
        ...state,
        drafts: {
          ...state.drafts,
          [action.payload.conversationId]: action.payload.text,
        },
      };

    case 'SET_SUBMITTING':
      return { ...state, isSubmitting: action.payload };

    case 'SET_ERROR':
      return { ...state, error: action.payload };

    case 'SET_LOADING_CONVERSATIONS':
      return { ...state, isLoadingConversations: action.payload };

    case 'SET_CONVERSATIONS':
      return { ...state, conversations: action.payload, isLoadingConversations: false };

    case 'ADD_CONVERSATION':
      return {
        ...state,
        conversations: [action.payload, ...state.conversations],
        activeConversationId: action.payload.id,
      };

    case 'UPDATE_CONVERSATION':
      return {
        ...state,
        conversations: state.conversations.map((c) =>
          c.id === action.payload.id
            ? {
                ...c,
                title: action.payload.title,
                model_id: action.payload.model_id !== undefined ? action.payload.model_id : c.model_id,
                updated_at: new Date().toISOString(),
              }
            : c
        ),
      };

    case 'REMOVE_CONVERSATION': {
      const remaining = state.conversations.filter((c) => c.id !== action.payload);
      const newActive =
        state.activeConversationId === action.payload
          ? remaining.length > 0
            ? remaining[0].id
            : null
          : state.activeConversationId;
      const newMsgs = { ...state.messagesByConversation };
      delete newMsgs[action.payload];
      return {
        ...state,
        conversations: remaining,
        activeConversationId: newActive,
        messagesByConversation: newMsgs,
      };
    }

    case 'SET_ACTIVE_CONVERSATION':
      return { ...state, activeConversationId: action.payload };

    case 'SET_LOADING_MESSAGES':
      return { ...state, isLoadingMessages: action.payload };

    case 'SET_MESSAGES':
      return {
        ...state,
        isLoadingMessages: false,
        messagesByConversation: {
          ...state.messagesByConversation,
          [action.payload.conversationId]: action.payload.messages,
        },
      };

    case 'APPEND_MESSAGE': {
      const convId = action.payload.conversationId;
      const existing = state.messagesByConversation[convId] || [];
      return {
        ...state,
        messagesByConversation: {
          ...state.messagesByConversation,
          [convId]: [...existing, action.payload.message],
        },
      };
    }

    case 'UPDATE_MESSAGE': {
      const convId = action.payload.conversationId;
      const existing = state.messagesByConversation[convId] || [];
      const updated = existing.map((m) =>
        m.id === action.payload.messageId ? { ...m, ...action.payload.updates } : m
      );
      return {
        ...state,
        messagesByConversation: {
          ...state.messagesByConversation,
          [convId]: updated,
        },
      };
    }

    case 'SET_LOADING_MODELS':
      return { ...state, isLoadingModels: action.payload };

    case 'SET_MODELS': {
      const models = action.payload.models;
      let selected = state.selectedModelId;
      if (models.length > 0 && !models.some((m) => m.id === selected)) {
        selected = models[0].id;
      }
      return {
        ...state,
        availableModels: models,
        modelError: action.payload.error || null,
        selectedModelId: selected,
        isLoadingModels: false,
      };
    }

    case 'START_STREAMING':
      return {
        ...state,
        isSubmitting: false,
        error: null,
        streamingState: {
          isStreaming: true,
          conversationId: action.payload.conversationId,
          messageId: action.payload.assistantMessageId,
          accumulatedText: '',
          tokenCount: 0,
          allocatedContext: null,
          abortController: action.payload.abortController,
          startTime: Date.now(),
          error: null,
        },
      };

    case 'STREAM_STARTED':
      return {
        ...state,
        streamingState: {
          ...state.streamingState,
          messageId: action.payload.assistantMessageId,
          allocatedContext: action.payload.allocatedContext,
        },
      };

    case 'APPEND_STREAM_DELTA': {
      const convId = action.payload.conversationId;
      const newText = state.streamingState.accumulatedText + action.payload.deltaText;
      const currentMsgs = state.messagesByConversation[convId] || [];
      const assistantId = state.streamingState.messageId;

      const updatedMsgs = assistantId
        ? currentMsgs.map((m) =>
            m.id === assistantId ? { ...m, content: newText, status: 'streaming' } : m
          )
        : currentMsgs;

      return {
        ...state,
        messagesByConversation: {
          ...state.messagesByConversation,
          [convId]: updatedMsgs,
        },
        streamingState: {
          ...state.streamingState,
          accumulatedText: newText,
          tokenCount: action.payload.tokenCount,
        },
      };
    }

    case 'FINISH_STREAM': {
      const convId = action.payload.conversationId;
      const assistantId = state.streamingState.messageId;
      const currentMsgs = state.messagesByConversation[convId] || [];

      const updatedMsgs = assistantId
        ? currentMsgs.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: state.streamingState.accumulatedText,
                  status: action.payload.status,
                  finish_reason: action.payload.finishReason || m.finish_reason,
                  prompt_tokens:
                    action.payload.promptTokens !== undefined
                      ? action.payload.promptTokens
                      : m.prompt_tokens,
                  completion_tokens:
                    action.payload.completionTokens !== undefined
                      ? action.payload.completionTokens
                      : m.completion_tokens,
                  error_message: action.payload.error || m.error_message,
                  updated_at: new Date().toISOString(),
                }
              : m
          )
        : currentMsgs;

      return {
        ...state,
        isSubmitting: false,
        error: action.payload.status === 'failed' ? action.payload.error || 'Generation failed' : null,
        messagesByConversation: {
          ...state.messagesByConversation,
          [convId]: updatedMsgs,
        },
        streamingState: initialStreamingState,
      };
    }

    default:
      return state;
  }
}

interface ChatContextType {
  state: ChatState;
  setActiveView: (view: 'chat' | 'system') => void;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  setSearchQuery: (query: string) => void;
  setSelectedModelId: (modelId: string) => void;
  setDraft: (conversationId: string, text: string) => void;
  setActiveConversationId: (id: string | null) => void;
  refreshConversations: () => Promise<void>;
  createConversation: (title?: string, modelId?: string) => Promise<ConversationResponse>;
  updateConversationTitle: (id: string, title: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  refreshMessages: (conversationId: string) => Promise<void>;
  refreshModels: () => Promise<void>;
  sendStreamingMessage: (content: string) => Promise<void>;
  cancelStreaming: () => Promise<void>;
  clearError: () => void;
}

const ChatContext = createContext<ChatContextType | undefined>(undefined);

export const ChatProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [state, dispatch] = useReducer(chatReducer, initialState);
  const streamingClientRef = useRef(new StreamingConversationClient(backendClient));
  const activeAbortControllerRef = useRef<AbortController | null>(null);

  const refreshConversations = useCallback(async () => {
    dispatch({ type: 'SET_LOADING_CONVERSATIONS', payload: true });
    try {
      const res = await conversationService.listConversations(100);
      dispatch({ type: 'SET_CONVERSATIONS', payload: res.conversations });
      if (res.conversations.length > 0 && !state.activeConversationId) {
        dispatch({ type: 'SET_ACTIVE_CONVERSATION', payload: res.conversations[0].id });
      }
    } catch (err) {
      console.error('Failed to load conversations:', err);
      dispatch({ type: 'SET_LOADING_CONVERSATIONS', payload: false });
    }
  }, [state.activeConversationId]);

  const refreshMessages = useCallback(async (conversationId: string) => {
    dispatch({ type: 'SET_LOADING_MESSAGES', payload: true });
    try {
      const res = await conversationService.listMessages(conversationId, 200);
      dispatch({
        type: 'SET_MESSAGES',
        payload: { conversationId, messages: res.messages },
      });
    } catch (err) {
      console.error(`Failed to load messages for ${conversationId}:`, err);
      dispatch({ type: 'SET_LOADING_MESSAGES', payload: false });
    }
  }, []);

  const refreshModels = useCallback(async () => {
    dispatch({ type: 'SET_LOADING_MODELS', payload: true });
    try {
      const res = await modelService.listModels();
      const err = Object.values(res.errors || {}).join('; ');
      dispatch({
        type: 'SET_MODELS',
        payload: { models: res.models || [], error: err || null },
      });
    } catch (err: unknown) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      dispatch({
        type: 'SET_MODELS',
        payload: { models: [], error: errorMsg },
      });
    }
  }, []);

  // Initial load
  useEffect(() => {
    refreshConversations();
    refreshModels();
  }, [refreshConversations, refreshModels]);

  // Load messages when active conversation changes
  useEffect(() => {
    if (state.activeConversationId && !state.messagesByConversation[state.activeConversationId]) {
      refreshMessages(state.activeConversationId);
    }
  }, [state.activeConversationId, state.messagesByConversation, refreshMessages]);

  const createConversation = async (title?: string, modelId?: string) => {
    const chosenModel = modelId || state.selectedModelId;
    const conv = await conversationService.createConversation({
      title: title || 'New Conversation',
      model_id: chosenModel,
    });
    dispatch({ type: 'ADD_CONVERSATION', payload: conv });
    return conv;
  };

  const updateConversationTitle = async (id: string, title: string) => {
    await conversationService.updateConversation(id, { title });
    dispatch({ type: 'UPDATE_CONVERSATION', payload: { id, title } });
  };

  const deleteConversation = async (id: string) => {
    await conversationService.deleteConversation(id);
    dispatch({ type: 'REMOVE_CONVERSATION', payload: id });
  };

  const cancelStreaming = async () => {
    const convId = state.streamingState.conversationId;
    if (activeAbortControllerRef.current) {
      activeAbortControllerRef.current.abort();
      activeAbortControllerRef.current = null;
    }
    if (convId) {
      try {
        await streamingClientRef.current.cancelTurn(convId);
      } catch (err) {
        console.debug('REST cancellation error (non-fatal):', err);
      }
      dispatch({
        type: 'FINISH_STREAM',
        payload: {
          conversationId: convId,
          status: 'interrupted',
          finishReason: 'cancelled',
        },
      });
    }
  };

  const sendStreamingMessage = async (content: string) => {
    const trimmed = content.trim();
    if (!trimmed || state.streamingState.isStreaming || state.isSubmitting) return;

    const modelToUse = state.selectedModelId;
    if (!modelToUse) {
      dispatch({
        type: 'SET_ERROR',
        payload: 'No AI model selected. Please select a model from the toolbar.',
      });
      return;
    }

    dispatch({ type: 'SET_SUBMITTING', payload: true });
    dispatch({ type: 'SET_ERROR', payload: null });

    let targetConvId = state.activeConversationId;
    if (!targetConvId) {
      try {
        const firstLine = trimmed.split('\n')[0].slice(0, 40);
        const title = firstLine.length > 0 ? firstLine : 'New Conversation';
        const created = await createConversation(title, modelToUse);
        targetConvId = created.id;
      } catch (err: unknown) {
        dispatch({ type: 'SET_SUBMITTING', payload: false });
        const errorMsg = err instanceof Error ? err.message : String(err);
        dispatch({
          type: 'SET_ERROR',
          payload: `Failed to create conversation: ${errorMsg}`,
        });
        return;
      }
    }

    const convId = targetConvId;

    // Optimistic user message
    const tempUserMsgId = `temp-user-${Date.now()}`;
    const userMessage: MessageResponse = {
      id: tempUserMsgId,
      conversation_id: convId,
      sequence: 9999,
      role: 'user',
      content: trimmed,
      status: 'completed',
      model_id: modelToUse,
      finish_reason: null,
      prompt_tokens: null,
      completion_tokens: null,
      error_message: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    dispatch({
      type: 'APPEND_MESSAGE',
      payload: { conversationId: convId, message: userMessage },
    });

    // Clear draft for this conversation and the 'new' draft
    dispatch({ type: 'SET_DRAFT', payload: { conversationId: convId, text: '' } });
    dispatch({ type: 'SET_DRAFT', payload: { conversationId: 'new', text: '' } });

    // Placeholder assistant message
    const tempAssistantId = `temp-assistant-${Date.now()}`;
    const assistantPlaceholder: MessageResponse = {
      id: tempAssistantId,
      conversation_id: convId,
      sequence: 10000,
      role: 'assistant',
      content: '',
      status: 'streaming',
      model_id: modelToUse,
      finish_reason: null,
      prompt_tokens: null,
      completion_tokens: null,
      error_message: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    dispatch({
      type: 'APPEND_MESSAGE',
      payload: { conversationId: convId, message: assistantPlaceholder },
    });

    const controller = new AbortController();
    activeAbortControllerRef.current = controller;
    dispatch({
      type: 'START_STREAMING',
      payload: {
        conversationId: convId,
        assistantMessageId: tempAssistantId,
        abortController: controller,
      },
    });

    const callbacks: StreamCallbacks = {
      onStarted: (payload) => {
        dispatch({
          type: 'STREAM_STARTED',
          payload: {
            conversationId: convId,
            assistantMessageId: tempAssistantId,
            allocatedContext: payload.allocated_context,
          },
        });
      },
      onDelta: (deltaText, payload) => {
        dispatch({
          type: 'APPEND_STREAM_DELTA',
          payload: {
            conversationId: convId,
            deltaText,
            tokenCount: payload.accumulated_tokens,
          },
        });
      },
      onCompleted: (payload) => {
        activeAbortControllerRef.current = null;
        dispatch({
          type: 'FINISH_STREAM',
          payload: {
            conversationId: convId,
            status: 'completed',
            finishReason: payload.finish_reason,
            promptTokens: payload.prompt_tokens,
            completionTokens: payload.completion_tokens,
          },
        });
      },
      onInterrupted: (payload) => {
        activeAbortControllerRef.current = null;
        dispatch({
          type: 'FINISH_STREAM',
          payload: {
            conversationId: convId,
            status: 'interrupted',
            finishReason: payload.reason,
          },
        });
      },
      onCancelled: (payload) => {
        activeAbortControllerRef.current = null;
        dispatch({
          type: 'FINISH_STREAM',
          payload: {
            conversationId: convId,
            status: 'interrupted',
            finishReason: payload.reason,
          },
        });
      },
      onFailed: (payload) => {
        activeAbortControllerRef.current = null;
        dispatch({
          type: 'FINISH_STREAM',
          payload: {
            conversationId: convId,
            status: 'failed',
            error: payload.error_message || (payload as any).error,
          },
        });
      },
      onError: (err) => {
        activeAbortControllerRef.current = null;
        dispatch({
          type: 'FINISH_STREAM',
          payload: {
            conversationId: convId,
            status: 'failed',
            error: err.message,
          },
        });
      },
    };

    try {
      await streamingClientRef.current.streamMessage(convId, content, callbacks, {
        modelId: modelToUse,
        abortController: controller,
      });
    } catch (err: unknown) {
      activeAbortControllerRef.current = null;
      const errorMsg = err instanceof Error ? err.message : String(err);
      dispatch({
        type: 'FINISH_STREAM',
        payload: {
          conversationId: convId,
          status: 'failed',
          error: errorMsg,
        },
      });
    }
  };

  const contextValue: ChatContextType = {
    state,
    setActiveView: (view) => dispatch({ type: 'SET_ACTIVE_VIEW', payload: view }),
    toggleSidebar: () => dispatch({ type: 'TOGGLE_SIDEBAR' }),
    setSidebarOpen: (open) => dispatch({ type: 'SET_SIDEBAR_OPEN', payload: open }),
    setSearchQuery: (q) => dispatch({ type: 'SET_SEARCH_QUERY', payload: q }),
    setSelectedModelId: (m) => dispatch({ type: 'SET_SELECTED_MODEL', payload: m }),
    setDraft: (id, text) => dispatch({ type: 'SET_DRAFT', payload: { conversationId: id, text } }),
    setActiveConversationId: (id) => dispatch({ type: 'SET_ACTIVE_CONVERSATION', payload: id }),
    refreshConversations,
    createConversation,
    updateConversationTitle,
    deleteConversation,
    refreshMessages,
    refreshModels,
    sendStreamingMessage,
    cancelStreaming,
    clearError: () => dispatch({ type: 'SET_ERROR', payload: null }),
  };

  return <ChatContext.Provider value={contextValue}>{children}</ChatContext.Provider>;
};

export function useChat(): ChatContextType {
  const context = useContext(ChatContext);
  if (!context) {
    throw new Error('useChat must be used within a ChatProvider');
  }
  return context;
}

