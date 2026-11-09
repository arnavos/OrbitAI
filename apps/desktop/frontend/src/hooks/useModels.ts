import { useMemo } from 'react';
import { useChat } from '../context/ChatContext';
import type { ModelDescriptorResponse } from '../types/conversation';

export function useModels() {
  const {
    state,
    setSelectedModelId,
    refreshModels,
  } = useChat();

  const selectedModel: ModelDescriptorResponse | null = useMemo(() => {
    return (
      state.availableModels.find((m) => m.id === state.selectedModelId) ||
      (state.availableModels.length > 0 ? state.availableModels[0] : null)
    );
  }, [state.availableModels, state.selectedModelId]);

  return {
    models: state.availableModels,
    selectedModel,
    selectedModelId: state.selectedModelId,
    isLoading: state.isLoadingModels,
    error: state.modelError,
    setSelectedModelId,
    refreshModels,
  };
}
